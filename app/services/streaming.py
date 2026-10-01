"""HTTP streaming of telegram-stored files with Range support (single or multi-part)."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
import typing
import zlib
from typing import AsyncIterator, Optional, Tuple

from ..core.db import Database
from ..core.models import FileRepo, now
from ..tg.base import BackendClient, TransferError
from ..tg.manager import TGManager


def _tmp_dir() -> str:
    from ..core.config import get_settings

    d = get_settings().final_tmp_dir()
    os.makedirs(d, exist_ok=True)
    return d

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def parse_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """Return inclusive (start, end) or None. Raises ValueError on unsatisfiable."""
    m = RANGE_RE.match(header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        # suffix range: last N bytes
        n = int(end_s)
        if n == 0:
            raise ValueError("unsatisfiable")
        start = max(0, size - n)
        return (start, size - 1)
    start = int(start_s)
    if start >= size:
        raise ValueError("unsatisfiable")
    end = int(end_s) if end_s else size - 1
    if end >= size:
        end = size - 1
    if end < start:
        raise ValueError("unsatisfiable")
    return (start, end)


async def _iter_single(
    backend: BackendClient, message_id: int, chat: str, *, start: int, end: Optional[int], size: Optional[int]
) -> AsyncIterator[bytes]:
    async for chunk in backend.iter_file(message_id, chat, start=start, end=end, size=size):
        yield chunk


async def _iter_multipart(
    backend: BackendClient,
    chat: str,
    parts: list,
    *,
    start: int,
    end: Optional[int],
) -> AsyncIterator[bytes]:
    """Concatenate parts virtually; slice [start, end] across part boundaries."""
    remaining = None if end is None else end - start + 1
    skip = start
    for part in parts:
        if remaining is not None and remaining <= 0:
            break
        psize = int(part["size"])
        if skip >= psize:
            skip -= psize
            continue
        take = psize - skip
        if remaining is not None:
            take = min(take, remaining)
        pos = skip
        async for chunk in backend.iter_file(
            part["message_id"], chat, start=pos, end=psize - 1, size=psize
        ):
            if remaining is not None and remaining <= 0:
                break
            if len(chunk) > take:
                chunk = chunk[:take]
            take -= len(chunk)
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
        skip = 0


async def file_response(
    *,
    db: Database,
    manager: TGManager,
    rec: dict,
    parts: list,
    range_header: Optional[str],
    filename: str,
    mime: str,
    head_only: bool = False,
    range_start: Optional[int] = None,
    inline: bool = False,
) -> "typing.Any":
    """Build a Starlette StreamingResponse with Range/206 handling.

    The telegram backend is acquired inside the generator so slow clients do
    not hold pool slots while queued (acquire happens right before first byte).
    ``inline=True`` serves browser-previewable media without the download
    attachment header (image/video/audio/pdf preview in the panel).
    """
    from fastapi.responses import StreamingResponse

    total = int(rec["size"])
    start, end = 0, total - 1
    status = 200
    disposition = "inline" if inline else "attachment"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{_ascii_name(filename)}"; filename*=UTF-8\'\'{_quote(filename)}',
        "ETag": f'"{rec["id"]}"',
        "Cache-Control": "public, max-age=3600",
    }

    if range_header:
        try:
            rng = parse_range(range_header, total)
        except ValueError:
            return typing.cast("typing.Any", _416(total))
        if rng is not None:
            start, end = rng
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"

    length = end - start + 1
    headers["Content-Length"] = str(length)

    file_id = rec["id"]
    storage_chat = rec["storage_chat"] or "me"

    if head_only:
        from fastapi.responses import Response

        return Response(status_code=status, headers=headers, media_type=mime)

    # Borrow the backend eagerly (must not await inside the response generator);
    # released after the response completes via the background task.
    from ..tg.base import TransferError
    try:
        borrowed = await manager.acquire("acc", backend=rec.get("backend") or "telegram")
    except Exception as exc:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": f"storage unavailable: {exc}"}, status_code=503)
    backend = borrowed.backend
    chat = storage_chat or getattr(backend, "storage_chat", "me")

    async def gen() -> AsyncIterator[bytes]:
        bg = resp.background
        try:
            if len(parts) <= 1 and parts:
                async for chunk in _iter_single(
                    backend, parts[0]["message_id"], chat, start=start, end=end, size=total if len(parts) == 1 else None
                ):
                    bg.bytes_sent += len(chunk)
                    yield chunk
            else:
                async for chunk in _iter_multipart(backend, chat, parts, start=start, end=end):
                    bg.bytes_sent += len(chunk)
                    yield chunk
        except TransferError as exc:
            bg.error = exc
            yield b""
            raise
        except Exception as exc:
            bg.error = exc
            yield b""
            raise

    resp = StreamingResponse(gen(), status_code=status, media_type=mime, headers=headers)
    resp.background = _ReleaseAndCount(manager, borrowed.key, db, file_id)
    return resp


def _416(total: int) -> "typing.Any":
    from fastapi.responses import Response

    return Response(
        status_code=416,
        headers={"Content-Range": f"bytes */{total}"},
        media_type="text/plain",
    )


def _ascii_name(name: str) -> str:
    return "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")


class _ZipBackground:
    """After a bulk-zip response completes: release the borrowed backend,
    unlink the temp file, and swallow client-abort errors quietly."""

    def __init__(self, manager: TGManager, key: str, tmp_path: Optional[str]) -> None:
        self.manager = manager
        self.key = key
        self.tmp_path = tmp_path
        self.error: Optional[BaseException] = None

    async def __call__(self) -> None:
        try:
            await self.manager.release(self.key, self.error)
        except Exception:
            pass
        if self.tmp_path:
            try:
                os.remove(self.tmp_path)
            except OSError:
                pass


async def zip_stream_response(
    *,
    db: Database,
    manager: TGManager,
    files: list,
) -> "typing.Any":
    """Stream several stored files as one temporary zip archive.

    Each entry is downloaded from telegram into a temp file (parts joined),
    then streamed into the client. The archive is never fully stored: only
    one entry exists on disk at a time and it is unlinked right after its
    bytes are emitted. Entries share one borrowed backend, released after
    the response completes.

    The zip is written by hand (local header with flag bit 3 = data
    descriptor, raw deflate, descriptor, then one central directory at the
    end) because zipfile.open(w) force-rewrites the local header afterwards —
    impossible on a non-seekable stream.
    """
    import struct
    import zipfile

    from fastapi.responses import StreamingResponse

    # one backend for the whole archive (uploads/other jobs queue behind it)
    borrowed = await manager.acquire("acc", backend=files[0].get("backend") or "telegram")
    backend = borrowed.backend
    chat = files[0]["storage_chat"] or getattr(backend, "storage_chat", "me")
    tmp_dir = _tmp_dir()
    tmp_paths: list = []

    async def fetch_entry(rec: dict) -> str:
        """Materialise one stored file (joined parts) into a temp file."""
        parts = await FileRepo(db).parts(rec["id"])
        path = os.path.join(tmp_dir, f"zip_{rec['id'].replace('/', '_')}.bin")
        tmp_paths.append(path)
        with open(path, "wb") as fh:
            for part in parts:
                async for chunk in backend.iter_file(
                    part["message_id"], chat, start=0, end=None, size=part["size"]
                ):
                    fh.write(chunk)
        return path

    def _cleanup_tmp() -> None:
        for p in tmp_paths:
            try:
                os.remove(p)
            except OSError:
                pass
        tmp_paths.clear()

    async def gen() -> AsyncIterator[bytes]:
        names_seen: set = set()
        centrals: list = []  # (name_b, crc, csize, usize, offset)
        offset = 0
        try:
            for rec in files:
                name = _unique_zip_name(rec["name"], names_seen)
                name_b = name.encode("utf-8")
                try:
                    path = await fetch_entry(rec)
                except TransferError as exc:
                    # a dead entry must not kill the whole archive: record a stub
                    stub_name = f"_errors/{name}.txt".encode("utf-8")
                    stub = f"download failed: {exc}\n".encode("utf-8")
                    crc0 = zlib.crc32(stub) & 0xFFFFFFFF
                    comp0 = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
                    body0 = comp0.compress(stub) + comp0.flush()
                    header0 = struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0x08, 8, 0, 0, 0, 0, 0, len(stub_name), 0) + stub_name
                    desc0 = struct.pack("<LLLL", 0x08074b50, crc0, len(body0), len(stub))
                    centrals.append((stub_name, crc0, len(body0), len(stub), offset))
                    offset += len(header0) + len(body0) + len(desc0)
                    yield header0 + body0 + desc0
                    continue
                with open(path, "rb") as fh:
                    comp = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
                    crc = 0
                    usize = 0
                    csize = 0
                    entry_offset = offset
                    # local file header (flag bit 3: sizes/CRC in the trailing
                    # data descriptor, so the header can be flushed as-is)
                    header = struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0x08, 8, 0, 0, 0, 0, 0, len(name_b), 0) + name_b
                    offset += len(header)
                    yield header
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        crc = zlib.crc32(chunk, crc)
                        usize += len(chunk)
                        piece = comp.compress(chunk)
                        if piece:
                            csize += len(piece)
                            offset += len(piece)
                            yield piece
                    piece = comp.flush()
                    if piece:
                        csize += len(piece)
                        offset += len(piece)
                        yield piece
                    descriptor = struct.pack("<LLLL", 0x08074b50, crc & 0xFFFFFFFF, csize, usize)
                    offset += len(descriptor)
                    yield descriptor
                centrals.append((name_b, crc & 0xFFFFFFFF, csize, usize, entry_offset))
                os.remove(path)
                tmp_paths.remove(path)
            # central directory + EOCD
            cd = io.BytesIO()
            cd_start = offset
            for name_b, crc, csize, usize, lho in centrals:
                cd.write(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, 20, 20, 0x08, 8, 0, 0, crc, csize, usize, len(name_b), 0, 0, 0, 0, (0o600 << 16), lho))
                cd.write(name_b)
            cd_data = cd.getvalue()
            cd_size = len(cd_data)
            offset += cd_size
            eocd = struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, len(centrals), len(centrals), cd_size, cd_start, 0)
            yield cd_data + eocd
        except GeneratorExit:
            _cleanup_tmp()
            raise
        except Exception as exc:
            _cleanup_tmp()
            bg.error = exc
            raise

    bg = _ZipBackground(manager, borrowed.key, None)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    resp = StreamingResponse(
        gen(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tgdrive-{stamp}.zip"'},
    )
    resp.background = bg
    return resp


def _unique_zip_name(name: str, seen: set) -> str:
    base = os.path.basename(name or "file").replace("\\", "_").replace("/", "_")[:150] or "file"
    candidate = base
    i = 1
    while candidate in seen:
        stem, dot, ext = base.rpartition(".")
        candidate = f"{stem or base}-{i}{dot}{ext}" if dot else f"{base}-{i}"
        i += 1
    seen.add(candidate)
    return candidate


class _ReleaseAndCount:
    """Release borrowed backend + count actually-served bytes after response completes.

    The streaming generator mutates ``bytes_sent``/``error`` live; Starlette
    runs ``__call__`` once the response is fully sent (or the client goes away).
    """

    def __init__(self, manager: TGManager, key: str, db: Database, file_id: str) -> None:
        self.manager = manager
        self.key = key
        self.db = db
        self.file_id = file_id
        self.bytes_sent = 0
        self.error: Optional[BaseException] = None

    async def __call__(self) -> None:
        served = self.bytes_sent
        try:
            await FileRepo(self.db).count_download(self.file_id, served)
        except Exception:
            pass
        try:
            await self.manager.release(self.key, self.error)
            if self.error is None and served > 0:
                await self.manager.note_bytes(self.key, served)
        except Exception:
            pass
