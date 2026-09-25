"""Real telegram backends implementing BackendClient.

- TelethonBackend: MTProto user account via StringSession.
- BotBackend: Bot API HTTP via Telethon's TelegramClient (bot_token) — uses
  multipart upload / getFile for small files. Kept here so the bot path shares
  the same interface.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Optional

from .base import BackendClient, FloodWait, SendFailure, TransferError
from . import fast_transfer

log = logging.getLogger("tgdrive.telethon")


def _map_error(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name in ("FloodWaitError",):
        return FloodWait(float(getattr(exc, "seconds", 30)))
    if name in ("FilePartsInvalidError", "FileMigrateError", "NetworkError", "TimeoutError"):
        return TransferError(str(exc))
    return SendFailure(str(exc) or name)


class TelethonBackend(BackendClient):
    kind = "telethon"

    def __init__(self, cid: str, session_string: str, *, api_id: int, api_hash: str, storage_chat: str = "me", proxy: Optional[tuple] = None) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self.id = cid
        self._api_id = api_id
        self._api_hash = api_hash
        self.storage_chat = storage_chat
        self._proxy = proxy
        self._client = TelegramClient(StringSession(session_string), api_id, api_hash, proxy=proxy)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self._client.connect)
        if not await asyncio.to_thread(self._client.is_user_authorized):
            raise TransferError("session not authorized")
        self._started = True

    def _location(self, message):
        doc = getattr(message, "document", None)
        if doc is None:
            raise TransferError("message has no document")
        return doc

    async def send_document(self, chat: str, path: str, name: str, mime: str, caption: str = "") -> dict:
        try:
            await self.start()
            msg = await fast_transfer.send_file_message(
                self._client, chat or self.storage_chat, path, caption=caption, file_name=name
            )
            doc = self._location(msg)
            return {"message_id": int(msg.id), "size": int(getattr(doc, "size", os.path.getsize(path)))}
        except FloodWait:
            raise
        except Exception as exc:  # telethon raises many error types
            raise _map_error(exc) from exc

    async def iter_file(
        self,
        message_id: int,
        chat: str,
        *,
        start: int,
        end: Optional[int],
        size: Optional[int],
    ) -> AsyncIterator[bytes]:
        try:
            await self.start()
            msg = await self._client.get_messages(
                chat or self.storage_chat, ids=message_id
            )
            if msg is None or msg.document is None:
                raise TransferError("message/document not found")
            doc = msg.document
            async for chunk in fast_transfer.stream_range(
                self._client,
                doc,
                start=start,
                end=end,
                size=int(getattr(doc, "size", size or 0)) or None,
            ):
                yield chunk
        except (FloodWait, TransferError):
            raise
        except Exception as exc:
            raise _map_error(exc) from exc

    async def delete_message(self, message_id: int, chat: str) -> None:
        try:
            await self.start()
            await fast_transfer.delete_messages(self._client, chat or self.storage_chat, [message_id])
        except Exception as exc:
            raise _map_error(exc) from exc

    async def close(self) -> None:
        try:
            await asyncio.to_thread(self._client.disconnect)
        except Exception:
            pass
        self._started = False


class BotBackend(BackendClient):
    """Bot API via plain HTTP (no heavy deps): sendDocument + getFile streaming."""

    kind = "bot"

    def __init__(self, cid: str, token: str, base: str = "https://api.telegram.org") -> None:
        import httpx

        self.id = cid
        self._token = token
        self._base = base.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base, timeout=120)

    async def _call(self, method: str, **params: Any) -> dict:
        resp = await self._http.post(f"/bot{self._token}/{method}", json=params)
        data = resp.json()
        if not data.get("ok"):
            desc = str(data.get("description", ""))
            retry = resp.headers.get("retry-after")
            if resp.status_code == 429 and retry:
                raise FloodWait(float(retry))
            if "Too Many Requests" in desc and retry:
                raise FloodWait(float(retry))
            raise SendFailure(desc or f"bot api error {resp.status_code}")
        return data["result"]

    async def send_document(self, chat: str, path: str, name: str, mime: str) -> dict:
        try:
            with open(path, "rb") as fh:
                files = {"document": (name, fh, mime or "application/octet-stream")}
                resp = await self._http.post(
                    f"/bot{self._token}/sendDocument", data={"chat_id": chat}, files=files
                )
            data = resp.json()
            if not data.get("ok"):
                desc = str(data.get("description", ""))
                retry = resp.headers.get("retry-after")
                if retry:
                    raise FloodWait(float(retry))
                raise SendFailure(desc)
            msg = data["result"]
            doc = msg.get("document", {})
            return {"message_id": int(msg["message_id"]), "size": int(doc.get("file_size", 0))}
        except FloodWait:
            raise
        except SendFailure:
            raise
        except Exception as exc:
            raise SendFailure(str(exc)) from exc

    async def iter_file(self, message_id: int, chat: str, *, start: int, end: Optional[int], size: Optional[int]) -> AsyncIterator[bytes]:
        # Bot API cannot read arbitrary messages; used only for files the bot itself sent.
        raise TransferError("bot backend cannot stream arbitrary messages")

    async def delete_message(self, message_id: int, chat: str) -> None:
        await self._call("deleteMessage", chat_id=chat, message_id=message_id)

    async def close(self) -> None:
        await self._http.aclose()
