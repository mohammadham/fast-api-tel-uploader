"""FastTelethon-style transfer helpers.

Parallel segmented download: the byte range is split into N aligned segments,
each fetched by its own `iter_download` stream (own pipeline/connection) and
written into its slot of the output file. Streaming to HTTP responses uses a
single aligned stream with Range support.

Uploads use Telethon's native pipelined `save_file` (512KB parts).
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


async def upload_file(client, in_path: str, *, file_name: str = "", progress=None) -> Any:
    """Upload a local file to Telegram (pipelined parts). Returns InputFile."""
    return await client.save_file(
        in_path,
        file_name=file_name or os.path.basename(in_path),
        part_size_kb=512,
        progress=progress,
        force_document=True,
    )


async def send_file_message(client, chat, in_path: str, *, caption: str = "", file_name: str = "") -> Any:
    """Upload and send as document message; returns the Message (has .id, .document)."""
    return await client.send_file(
        chat,
        in_path,
        caption=caption,
        file_name=file_name or os.path.basename(in_path),
        force_document=True,
    )


async def delete_messages(client, chat, message_ids: list[int]) -> None:
    await client.delete_messages(chat, message_ids)
