"""In-memory FakeTelegram backend — used for tests and TGDRIVE_FAKE_TG=1 dev mode.

No network: uploads are stored in a dict keyed by (chat, message_id), keeping
the exact bytes, so download/stream/delete behave like the real thing.
"""
from __future__ import annotations

import itertools
import os
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from .base import BackendClient, FloodWait, SendFailure, TransferError

counter = itertools.count(1)


class FakeBackend(BackendClient):
    # ponytail: kind derives from cid prefix (eit: → eitaa) so routing tests need no second class
    @property
    def kind(self) -> str:  # type: ignore[override]
        return "eitaa" if self.id.startswith("eit:") else "fake"

    # shared store: (chat, message_id) → bytes ; simulates telegram servers
    STORE: Dict[Tuple[str, int], Tuple[bytes, str]] = {}

    def __init__(self, cid: str, *, fail_rate: float = 0.0, flood_seconds: float = 0.0) -> None:
        self.id = cid
        self.fail_rate = fail_rate
        self.flood_seconds = flood_seconds
        self.closed = False

    async def send_document(self, chat: str, path: str, name: str, mime: str) -> dict:
        import asyncio

        if self.fail_rate > 0:
            import random

            if random.random() < self.fail_rate:
                raise SendFailure("injected failure")
        if self.flood_seconds > 0:
            raise FloodWait(self.flood_seconds)
        with open(path, "rb") as fh:
            data = fh.read()
        mid = next(counter)
        self.STORE[(chat, mid)] = (data, mime)
        await asyncio.sleep(0)  # yield to event loop
        return {"message_id": mid, "size": len(data)}

    async def iter_file(
        self,
        message_id: int,
        chat: str,
        *,
        start: int,
        end: Optional[int],
        size: Optional[int],
    ) -> AsyncIterator[bytes]:
        entry = self.STORE.get((chat, message_id))
        if entry is None:
            raise TransferError(f"fake message {chat}/{message_id} not found")
        data, _mime = entry
        if end is None:
            end = len(data) - 1
        chunk = 256 * 1024
        pos = start
        while pos <= end:
            piece = data[pos : min(pos + chunk, end + 1)]
            if not piece:
                break
            yield piece
            pos += len(piece)

    async def delete_message(self, message_id: int, chat: str) -> None:
        self.STORE.pop((chat, message_id), None)

    async def close(self) -> None:
        self.closed = True
