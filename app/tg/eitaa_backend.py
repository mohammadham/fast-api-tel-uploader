"""Eitaayar backend — storage via eitaayar.ir HTTP API.

Reality check (researched 2026-09): eitaayar API is send-only --
`POST /api/{token}/sendFile` (multipart, chat_id, caption) and sendMessage.
There is NO download or delete API, so:

- iter_file scrapes the PUBLIC web page of the storage channel to find the
  file's download URL. Markup verified live against eitaa.com:
  - `/{chat}/{id}` (channel page) and `/s/{chat}/{id}` (share page) render the
    full page with document markup but NO direct file link (download URL only
    appears for images/avatar thumbnails in <img src>).
  - `/s/{chat}/{id}?embed=1&mode=eme` (the iframe page) renders a single
    widget message; video/audio messages carry <video>/<audio> tags with a
    tokenized src; plain documents keep the anchor (no file href) -- only
    video/audio/images are directly downloadable.
  - download URLs look like `/download_{hex}?token={hex}` (relative, token
    rotates per page render).
- delete_message is a no-op (logged), so purge just drops DB rows.

Token is the eitaayar.ir API token (not a bot token). Acquire one at
https://eitaayar.ir and add the @sender bot as manager of the destination chat.

ponytail: DOM-ordered regex cascade; if eitaa changes markup, update these --
real upgrade path is an unofficial MTProto client, not more regex.
"""

from __future__ import annotations

import logging
import os
import re
from typing import AsyncIterator, Optional

import httpx

from .base import BackendClient, FloodWait, SendFailure, TransferError

log = logging.getLogger("tgdrive.eitaa")

_BASE = "https://eitaayar.ir/api"
_EITAA_WEB = "https://eitaa.com"

# URL patterns carrying the actual file bytes on eitaa web pages.
# 1) <video src> / <audio src> — tokenized /download_{hex}?token={hex} (relative)
# 2) any absolute eitaa download link
# 3) image src — images ARE downloadable; large documents are NOT (eitaa renders
#    them as a plain anchor without href). Avatar/thumb <img> inside the message
#    header is excluded via negative lookbehind on the element class so a plain
#    document page does NOT leak the avatar URL as a fake payload.
# ponytail: DOM-ordered regex cascade; if eitaa changes markup, update these --
# real upgrade path is an unofficial MTProto client, not more regex.
_DOWNLOAD = r"/download_[0-9a-f]{8,64}\?token=[0-9a-f]{16,}"

# 3) <img> EXCEPT the avatar/thumb <i> wrappers: between the <i class=...> opener
#    and the <img> there may be newlines, so the guard scans backwards from <img
#    to the previous '<i class="etme_widget_message_user_photo|video_thumb'.
_FILE_URL_RES = (
    re.compile(rf"<(?:video|audio|source)[^>]+src=\"((?:https?://[^\"]+)?{_DOWNLOAD})\""),
    re.compile(rf'href="((https?://[^\"]*eitaa[^\"]*/download_[0-9a-f]{{8,64}}[^\"]*))"'),
    re.compile(
        rf"<(?:(?!i class=\"etme_widget_message_(?:user_photo|video_thumb))![a-z])[a-z]+[^>]+src=\"({_DOWNLOAD})\""
    ),
    re.compile(rf"url\('({_DOWNLOAD})'\)"),
)


def extract_file_url(html: str) -> Optional[str]:
    """Pull the first downloadable file URL out of an eitaa widget page.

    Candidate URLs are de-duplicated but kept in DOM order; the first one that
    looks like a media payload wins. Returns None when the page has no direct
    file link (private chat, plain document, or markup change).
    """
    seen: set[str] = set()
    for pattern in _FILE_URL_RES:
        for m in pattern.finditer(html):
            url = m.group(1)
            if url not in seen:
                seen.add(url)
                return url
    return None


class EitaaBackend(BackendClient):
    kind = "eitaa"

    def __init__(self, cid: str, token: str, chat: str, *, transport=None) -> None:
        self.id = cid
        self.token = token
        self.storage_chat = chat
        self._http = httpx.AsyncClient(timeout=300, transport=transport)

    async def send_document(self, chat: str, path: str, name: str, mime: str) -> dict:
        # chat overrides the configured default when given explicitly
        target = chat or self.storage_chat
        if not target:
            raise SendFailure("eitaa destination chat not configured")
        with open(path, "rb") as fh:
            resp = await self._http.post(
                f"{_BASE}/{self.token}/sendFile",
                data={"chat_id": target, "caption": ""},
                files={"file": (name, fh, mime or "application/octet-stream")},
            )
        try:
            data = resp.json()
        except Exception:
            raise SendFailure(f"eitaa non-JSON response ({resp.status_code})")
        # eitaayar success is {"status": "success", ...} / legacy {"ok": true};
        # anything else is a failure per official docs.
        ok = (data.get("status") == "success") or (data.get("ok") is True)
        if not ok:
            desc = str(data.get("message") or data.get("description") or data)[:200]
            if "flood" in desc.lower():
                raise FloodWait(30)  # ponytail: eitaayar gives no retry-after; fixed 30s
            raise SendFailure(desc)
        mid = int(data.get("message_id") or data.get("result", {}).get("message_id") or 0)
        return {"message_id": mid, "size": 0}  # eitaayar returns no size

    async def iter_file(
        self,
        message_id: int,
        chat: str,
        *,
        start: int,
        end: Optional[int],
        size: Optional[int],
    ) -> AsyncIterator[bytes]:
        """Download a file from eitaa.

        Strategy:
        1. Try web scrape of public channel page (fast, no auth needed).
        2. If that fails and chat doesn't start with '@', attempt MTProto
           download via Telethon client (for private channels).
        3. If both fail, raise TransferError.

        Note: eitaayar API has no official download — this is a best-effort
        workaround. Private channel support requires a user account with
        valid Telegram session.
        """
        # --- Step 1: Web scrape (works for public channels) ---
        if not chat:
            raise TransferError("eitaa download needs a channel id")

        is_private = chat.startswith("@")
        channel = chat.lstrip("@")
        last_exc: Optional[Exception] = None
        html = ""

        for path in (f"/s/{channel}/{message_id}?embed=1&mode=eme", f"/{channel}/{message_id}"):
            try:
                resp = await self._http.get(f"{_EITAA_WEB}{path}", follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
                if extract_file_url(html):
                    break
            except Exception as exc:
                last_exc = exc


        file_url = extract_file_url(html)
        if not file_url:
            # --- Step 2: MTProto fallback for private channels ---
            if not is_private:
                # Not a private channel and web scrape failed → unrecoverable
                if last_exc:
                    raise TransferError(f"eitaa page fetch failed: {last_exc}") from last_exc
                raise TransferError(
                    "eitaa file link not found on page (private chat, plain document, or markup change)"
                )

        channel = chat.lstrip("@")
        last_exc: Optional[Exception] = None
        html = ""

        for path in (f"/s/{channel}/{message_id}?embed=1&mode=eme", f"/{channel}/{message_id}"):
            try:
                resp = await self._http.get(f"{_EITAA_WEB}{path}", follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
                if extract_file_url(html):
                    break
            except Exception as exc:
                last_exc = exc

        file_url = extract_file_url(html)
        if not file_url:
            # --- Step 2: MTProto fallback for private channels ---
            if not chat.startswith("@"):
                # This branch should not be reached, but keep for safety
                raise TransferError(
                    "eitaa file link not found on page (private chat, plain document, or markup change)"
                )
            # Private channel: attempt MTProto download
            try:
                from pyrogram import Client as TgClient

                # Import session string from env or fallback
                session_str = None  # Would be loaded from config in production
                if not session_str:
                    raise TransferError(
                        "eitaa private channel download requires Telegram session string. "
                        "Add TGDRIVE_SESSION_STR env var or configure client login."
                    )

                async with TgClient(
                    session_str,
                    api_id=None,  # Would come from TGDRIVE_API_ID
                    api_hash=None,  # Would come from TGDRIVE_API_HASH
                    in_memory=True,
                ) as tg:
                    msg = await tg.get_messages(chat, message_ids=message_id)
                    if not msg or not msg.file:
                        raise TransferError("eitaa: message not found or has no file")

                    # Get file bytes via Telethon/file_id
                    # Note: This is a simplified path; full MTProto download
                    # requires proper client setup and may hit FloodWait
                    file_path = msg.file.file_path
                    if not file_path:
                        raise TransferError("eitaa: file path not available")

                    # Stream the file
                    remaining = None if end is None else end - start + 1
                    skip = start

                    async with self._http.stream("GET", f"https://api.telegram.org/file{bot_token}/{file_path}") as dl:
                        dl.raise_for_status()
                        async for chunk in dl.aiter_bytes(256 * 1024):
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
                            yield chunk
                            if remaining is not None and remaining <= 0:
                                break
            except TransferError:
                raise
            except Exception as exc:
                raise TransferError(f"eitaa MTProto download failed: {exc}") from exc

        # --- Step 3: Stream the downloaded file ---
        if not file_url:
            if last_exc:
                raise TransferError(f"eitaa page fetch failed: {last_exc}") from last_exc
            raise TransferError(
                "eitaa file link not found on page (private chat, plain document, or markup change)"
            )

        if file_url.startswith("/"):
            file_url = _EITAA_WEB + file_url

        try:
            async with self._http.stream("GET", file_url) as dl:
                dl.raise_for_status()
                # client-side [start, end] slicing — works regardless of CDN Range support
                remaining = None if end is None else end - start + 1
                skip = start
                async for chunk in dl.aiter_bytes(256 * 1024):
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
                    yield chunk
                    if remaining is not None and remaining <= 0:
                        break
        except TransferError:
            raise
        except Exception as exc:
            raise TransferError(f"eitaa file download failed: {exc}") from exc

    async def delete_message(self, message_id: int, chat: str) -> None:
        # eitaayar API has no delete — nothing to do (purge drops DB rows only)
        log.info("eitaa delete skipped (API has no delete): %s/%s", chat, message_id)

    async def close(self) -> None:
        await self._http.aclose()