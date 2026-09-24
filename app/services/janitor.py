"""Periodic janitor: stale tmp files, expired upload sessions, old finished jobs."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING

from ..core.config import get_settings
from ..core.models import UploadSessionRepo

if TYPE_CHECKING:  # pragma: no cover
    from ..core.db import Database
    from ..core.state import State

log = logging.getLogger("tgdrive.janitor")


class Janitor:
    def __init__(self, db: "Database") -> None:
        self.db = db
        self._task: asyncio.Task | None = None

    # state.queue/state.manager are read lazily to avoid a circular import

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="janitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("janitor sweep failed: %s", exc)
            await asyncio.sleep(600)

    async def _sweep(self) -> None:
        s = get_settings()
        tmp_dir = s.final_tmp_dir()
        cutoff = time.time() - 3600  # tmp files older than 1h
        removed = 0
        if os.path.isdir(tmp_dir):
            for name in os.listdir(tmp_dir):
                path = os.path.join(tmp_dir, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
        # expired upload sessions (runtime TTL setting wins over env)
        try:
            from ..core.settings_service import get_runtime

            ttl_min = int(await get_runtime(self.db, "upload_session_ttl_minutes") or s.upload_session_ttl_minutes)
        except Exception:
            ttl_min = s.upload_session_ttl_minutes
        stale = await UploadSessionRepo(self.db).stale(ttl_min * 60)
        for sess in stale:
            await UploadSessionRepo(self.db).delete(sess["id"])
            path = os.path.join(tmp_dir, f"{sess['id']}.part")
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        # old finished jobs (keep 24h)
        from ..core.models import JobRepo

        deleted = await JobRepo(self.db).purge_finished(time.time() - 86400)
        # trash: files soft-deleted more than 7 days ago → purge from telegram too
        from ..core.models import FileRepo, KIND_DELETE

        trashed = await FileRepo(self.db).trashed(time.time() - 7 * 86400)
        purged = 0
        for f in trashed:
            if state.queue and state.manager:  # only when app context is live
                await state.queue.enqueue(KIND_DELETE, {"file_id": f["id"]}, 10)
                purged += 1
        if removed or stale or deleted or purged:
            log.info("janitor: %s tmp, %s sessions, %s jobs, %s trash-purged", removed, len(stale), deleted, purged)
