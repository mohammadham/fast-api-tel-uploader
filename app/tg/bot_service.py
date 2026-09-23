"""Bot management service.

Polls Bot API getUpdates manually (no aiogram runtime dependency) and wires
commands to the shared priority queue. Multiple bots supported. The bot has a
lower effective priority than user downloads because it enqueues with the
same job kinds but never bypasses the queue.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import httpx

from ..core.config import get_settings
from ..core.models import KIND_DOWNLOAD, KIND_UPLOAD, PRIO_DOWNLOAD, PRIO_UPLOAD
from .telethon_backend import BotBackend

if TYPE_CHECKING:  # pragma: no cover
    from .manager import TGManager
    from ..queue.queue_manager import QueueManager
    from ..core.db import Database

log = logging.getLogger("tgdrive.bot")


class BotService:
    def __init__(self, db: "Database", manager: "TGManager", queue: "QueueManager") -> None:
        self.db = db
        self.manager = manager
        self.queue = queue
        self._tasks: Dict[int, asyncio.Task] = {}
        self._offsets: Dict[int, int] = {}
        self._http = httpx.AsyncClient(timeout=65)

    # ---------- lifecycle ----------
    async def start_all(self) -> None:
        from ..core.models import BotRepo

        for row in await BotRepo(self.db).list():
            if row["enabled"] and row["status"] == "ready":
                self.start_one(row["id"])

    def start_one(self, bot_id: int) -> None:
        if bot_id in self._tasks and not self._tasks[bot_id].done():
            return
        self._tasks[bot_id] = asyncio.create_task(self._poll_loop(bot_id), name=f"bot-{bot_id}")

    async def stop_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for bot_id in list(self._tasks):
            try:
                await self._tasks[bot_id]
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def verify_and_start(self, bot_id: int, token: str, label: str) -> Optional[int]:
        """Validate token via getMe, store encrypted, start polling."""
        base = get_settings().bot_api_base.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(f"{base}/bot{token}/getMe")
                data = resp.json()
        except Exception as exc:
            log.warning("bot getMe failed: %s", exc)
            return None
        if not data.get("ok"):
            return None
        username = data["result"].get("username", label)
        from ..core.models import BotRepo
        from ..core.security import encrypt_str

        await BotRepo(self.db).set_status(bot_id, "ready", "")
        self.start_one(bot_id)
        return bot_id

    # ---------- polling ----------
    async def _poll_loop(self, bot_id: int) -> None:
        from ..core.models import BotRepo
        from ..core.security import decrypt_str

        row = await BotRepo(self.db).get(bot_id)
        if not row:
            return
        token = decrypt_str(row["token_enc"])
        base = get_settings().bot_api_base.rstrip("/")
        last_err = 0.0
        while True:
            try:
                params = {"timeout": 50, "offset": self._offsets.get(bot_id, 0), "allowed_updates": '["message"]'}
                resp = await self._http.get(f"{base}/bot{token}/getUpdates", params=params)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(str(data.get("description", "getUpdates failed")))
                for update in data.get("result", []):
                    self._offsets[bot_id] = update["update_id"] + 1
                    try:
                        await self._handle_update(bot_id, token, update)
                    except Exception as exc:
                        log.exception("bot update handling failed: %s", exc)
                await BotRepo(self.db).set_status(bot_id, "ready", "")
                last_err = 0.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if time.time() - last_err > 60:
                    log.warning("bot %s poll error: %s", bot_id, exc)
                    last_err = time.time()
                    try:
                        await BotRepo(self.db).set_status(bot_id, "error", str(exc))
                    except Exception:
                        pass
                await asyncio.sleep(3)

    # ---------- command handling ----------
    def _allowed(self, update: dict) -> bool:
        allowed = get_settings().bot_admin_id_set
        if not allowed:
            return True
        msg = update.get("message", {})
        frm = msg.get("from", {})
        return int(frm.get("id", 0)) in allowed

    async def _reply(self, token: str, chat_id: Any, text: str) -> None:
        try:
            await self._http.post(
                f"/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"},
            )
        except Exception:
            pass

    async def _handle_update(self, bot_id: int, token: str, update: dict) -> None:
        msg = update.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None or not self._allowed(update):
            return
        text = (msg.get("text") or "").strip()
        doc = msg.get("document")

        if text.startswith("/start") or text.startswith("/help"):
            await self._reply(
                token,
                chat_id,
                "<b>TelegramDrive Bot</b>\n"
                "/upload — reply to a document with this to store it (≤50MB)\n"
                "/download file_id — get the file back\n"
                "/status — queue status\n"
                "/files — last files",
            )
        elif text.startswith("/upload") and doc:
            file_id = doc.get("file_id", "")
            name = doc.get("file_name", f"tgfile_{doc.get('file_size', 0)}")
            size = int(doc.get("file_size", 0))
            if size > 50 * 1024 * 1024:
                await self._reply(token, chat_id, "⚠️ Bot API supports files ≤50MB. Use accounts/API for bigger files.")
                return
            await self._enqueue_bot_upload(bot_id, token, chat_id, file_id, name, size)
        elif text.startswith("/download"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await self._reply(token, chat_id, "Usage: /download file_id")
                return
            await self._handle_bot_download(bot_id, token, chat_id, parts[1].strip())
        elif text.startswith("/status"):
            stats = await self.queue.stats()
            await self._reply(token, chat_id, f"📊 Queue: <code>{stats}</code>")
        elif text.startswith("/files"):
            rows = await self.db.fetch_all(
                "SELECT id, name, size, status FROM files ORDER BY created_at DESC LIMIT 5"
            )
            lines = [f"<code>{r['id']}</code> — {r['name']} ({r['size']}B, {r['status']})" for r in rows]
            await self._reply(token, chat_id, "\n".join(lines) or "no files yet")
        else:
            await self._reply(token, chat_id, "Unknown command. /help")

    async def _enqueue_bot_upload(self, bot_id: int, token: str, chat_id: Any, tg_file_id: str, name: str, size: int) -> None:
        import uuid

        from ..core.models import FileRepo, new_id

        file_id = new_id("f")
        await FileRepo(self.db).create(file_id, name, size, "application/octet-stream", uploader=f"bot:{bot_id}", source="bot")
        payload = {"file_id": file_id, "tg_file_id": tg_file_id, "bot_token_ref": bot_id, "chat_id": chat_id, "size": size}
        await self.queue.enqueue(KIND_UPLOAD, payload, PRIO_UPLOAD)
        await self._reply(token, chat_id, f"📥 Queued upload: <code>{file_id}</code>")

    async def _handle_bot_download(self, bot_id: int, token: str, chat_id: Any, file_id: str) -> None:
        from ..core.models import FileRepo

        rec = await FileRepo(self.db).get(file_id)
        if not rec or rec["status"] != "ready":
            await self._reply(token, chat_id, "❌ File not found or not ready.")
            return
        if rec["size"] > 50 * 1024 * 1024:
            link = f"{get_settings().public_base_url}/d/{file_id}"
            await self._reply(token, chat_id, f"📏 Too big for bot (≤50MB). Direct link: {link}")
            return
        await self.queue.enqueue(
            KIND_DOWNLOAD,
            {"file_id": file_id, "deliver_to": {"bot": bot_id, "chat_id": chat_id}},
            PRIO_DOWNLOAD,
        )
        await self._reply(token, chat_id, "⏳ Queued for delivery…")

    async def deliver_download(self, bot_id: int, chat_id: Any, local_path: str, name: str) -> None:
        from ..core.models import BotRepo
        from ..core.security import decrypt_str

        row = await BotRepo(self.db).get(bot_id)
        if not row:
            raise RuntimeError(f"bot {bot_id} not found")
        tok = decrypt_str(row["token_enc"])
        base = get_settings().bot_api_base.rstrip("/")
        with open(local_path, "rb") as fh:
            files = {"document": (name, fh, "application/octet-stream")}
            await self._http.post(f"{base}/bot{tok}/sendDocument", data={"chat_id": chat_id}, files=files)
