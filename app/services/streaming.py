"""HTTP streaming of telegram-stored files with Range support (single or multi-part)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import typing
from typing import AsyncIterator, Optional, Tuple

from ..core.db import Database
from ..core.models import FileRepo, now
from ..tg.base import BackendClient, TransferError
from ..tg.manager import TGManager

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
) -> "typing.Any":
    """Build a Starlette StreamingResponse with Range/206 handling.

    The telegram backend is acquired inside the generator so slow clients do
    not hold pool slots while queued (acquire happens right before first byte).
    """
    from fastapi.responses import StreamingResponse

    total = int(rec["size"])
    start, end = 0, total - 1
    status = 200
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{_ascii_name(filename)}"; filename*=UTF-8\'\'{_quote(filename)}',
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
