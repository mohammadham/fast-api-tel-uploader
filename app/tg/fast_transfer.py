"""FastTelethon-style transfer helpers.

Parallel segmented download: the byte range is split into N aligned segments,
each fetched by its own `iter_download` stream (own pipeline/connection) and
written into its slot of the output file. Streaming to HTTP responses uses a
single aligned stream with Range support.

Uploads split the file into 512KB parts and send independent
`SaveBigFilePart` requests concurrently.
"""
from __future__ import annotations

import asyncio
import math
import os
from typing import Any, AsyncIterator, Callable, Optional

ALIGN = 4096
MAX_REQUEST = 512 * 1024  # Telegram per-request cap (safe classic value)


def _align_down(x: int) -> int:
    return x - (x % ALIGN)


async def download_to_file(
    client,
    location,
    out_path: str,
    *,
    size: Optional[int] = None,
    start: int = 0,
    end: Optional[int] = None,
    workers: int = 6,
    chunk_cb: Optional[Callable[[int], None]] = None,
) -> int:
    """Download bytes [start, end) of `location` into out_path in parallel.

    The file is preallocated to `size` when provided (full-file mode).
    Returns bytes written.
    """
    end = size if (end is None and size) else end
    if end is None:
        end = start + (1 << 62)  # unknown size → single stream until EOF
    span = max(0, end - start)
    if span == 0:
        return 0

    fh = open(out_path, "wb")
    try:
        if size:
            fh.truncate(size)
        written = 0

        def _bump(n: int) -> None:
            nonlocal written
            written += n
            if chunk_cb:
                chunk_cb(n)

        if span <= MAX_REQUEST or workers <= 1:
            pos = start
            remaining: Optional[int] = span if size else None
            async for chunk in client.iter_download(
                location, offset=pos, file_size=size, request_size=MAX_REQUEST
            ):
                if remaining is not None:
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    remaining -= len(chunk)
                fh.seek(pos)
                fh.write(chunk)
                pos += len(chunk)
                _bump(len(chunk))
        else:
            nseg = workers
            seg = _align_down(math.ceil(span / nseg)) or ALIGN
            bounds: list[tuple[int, int]] = []
            pos = start
            while pos < end:
                stop = min(pos + seg, end)
                bounds.append((pos, stop))
                pos = stop

            async def one(off0: int, off1: int) -> None:
                p = off0
                left = off1 - off0
                async for chunk in client.iter_download(
                    location, offset=p, file_size=size, request_size=MAX_REQUEST
                ):
                    if left <= 0:
                        break
                    if len(chunk) > left:
                        chunk = chunk[:left]
                    left -= len(chunk)
                    fh.seek(p)
                    fh.write(chunk)
                    p += len(chunk)
                    _bump(len(chunk))

            await asyncio.gather(*(one(a, b) for a, b in bounds))
        return written
    finally:
        fh.close()


async def stream_range(
    client,
    location,
    *,
    start: int,
    end: Optional[int],
    size: Optional[int] = None,
    chunk_size: int = 256 * 1024,
) -> AsyncIterator[bytes]:
    """Async byte stream of [start, end] inclusive (HTTP Range semantics).

    Offsets are aligned down to 4096 (Telegram requirement); the difference is
    skipped locally.
    """
    aligned = _align_down(start)
    skip = start - aligned
    remaining = None if end is None else end - start + 1
    async for chunk in client.iter_download(
        location, offset=aligned, file_size=size, request_size=MAX_REQUEST
    ):
        if skip:
            if len(chunk) <= skip:
                skip -= len(chunk)
                continue
            chunk = chunk[skip:]
            skip = 0
        if remaining is not None:
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            remaining -= len(chunk)
        if chunk:
            yield chunk


async def upload_file(
    client,
    in_path: str,
    *,
    file_name: str = "",
    part_workers: int = 4,
    progress=None,
) -> Any:
    """Upload a local file to Telegram using parallel SaveBigFilePart requests.

    Telethon's own ``upload_file`` sends parts strictly serially.  For large
    files that leaves most of the link idle, so we issue independent part
    requests up to ``part_workers`` at a time.  Telegram accepts parts in any
    order; the returned ``InputFileBig`` carries the part count needed by
    ``send_file``.
    """
    from telethon import helpers
    from telethon.tl import functions, types

    file_name = file_name or os.path.basename(in_path)
    size = os.path.getsize(in_path)
    if size <= 10 * 1024 * 1024:
        # Small files use Telethon's InputSizedFile path (MD5 + exact size).
        return await client.upload_file(
            in_path, part_size_kb=512, file_name=file_name,
            progress_callback=progress,
        )

    part_size = MAX_REQUEST
    part_count = (size + part_size - 1) // part_size
    file_id = helpers.generate_random_long()
    workers = max(1, min(int(part_workers or 1), 16, part_count))
    sent = 0
    sent_lock = asyncio.Lock()

    async def send_part(worker_index: int) -> None:
        nonlocal sent
        with open(in_path, "rb") as fh:
            for index in range(worker_index, part_count, workers):
                fh.seek(index * part_size)
                part = fh.read(part_size)
                if len(part) != part_size and index < part_count - 1:
                    raise IOError(
                        f"short read at part {index}: {len(part)}/{part_size}"
                    )
                ok = await client(
                    functions.upload.SaveBigFilePartRequest(
                        file_id, index, part_count, part
                    )
                )
                if not ok:
                    raise RuntimeError(f"failed to upload file part {index}")
                async with sent_lock:
                    sent += len(part)
                    if progress:
                        await helpers._maybe_await(progress(sent, size))

    await asyncio.gather(*(send_part(i) for i in range(workers)))
    return types.InputFileBig(file_id, part_count, file_name)


async def send_file_message(
    client,
    chat,
    in_path: str,
    *,
    caption: str = "",
    file_name: str = "",
    mime: str = "",
    part_workers: int = 4,
    progress=None,
) -> Any:
    """Upload and send as document message; returns the Message (has .id, .document)."""
    from telethon.tl import types

    file_name = file_name or os.path.basename(in_path)
    handle = await upload_file(
        client, in_path, file_name=file_name,
        part_workers=part_workers, progress=progress,
    )
    return await client.send_file(
        chat,
        handle,
        caption=caption,
        force_document=True,
        attributes=[types.DocumentAttributeFilename(file_name=file_name)],
        mime_type=mime or None,
    )


async def delete_messages(client, chat, message_ids: list[int]) -> None:
    await client.delete_messages(chat, message_ids)
