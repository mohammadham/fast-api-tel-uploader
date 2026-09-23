"""Abstract telegram backend interface used by queue workers and APIs."""
from __future__ import annotations

import abc
from typing import Any, AsyncIterator, Optional


class BackendClient(abc.ABC):
    """One connected client (account session or bot) able to move files."""

    id: str

    @abc.abstractmethod
    async def send_document(self, chat: str, path: str, name: str, mime: str) -> dict:
        """Upload `path` as a document to `chat`. Returns {'message_id': int, 'size': int}."""

    @abc.abstractmethod
    async def iter_file(self, message_id: int, chat: str, *, start: int, end: Optional[int], size: Optional[int]) -> AsyncIterator[bytes]:
        """Yield bytes of the stored document for HTTP streaming."""

    @abc.abstractmethod
    async def delete_message(self, message_id: int, chat: str) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class SendFailure(Exception):
    pass


class FloodWait(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"flood wait {seconds}s")
        self.seconds = seconds


class TransferError(Exception):
    pass
