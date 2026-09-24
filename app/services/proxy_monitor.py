"""Periodic proxy health monitor.

Runs proxy speed tests every N minutes (runtime setting proxy_monitor_interval,
0=off) and notifies admins via Telegram when a proxy's state flips
(ok→down, down→ok, degraded↔ok, ...). Best-effort: probe/notify failures are
logged, never raised; monitor dies only when cancelled.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict

from ..core.models import ProxyRepo

log = logging.getLogger("tgdrive.proxy_monitor")

STATUS_LABELS = {
    "ok": "سالم",
    "degraded": "ضعیف",
    "down": "قطع",
    "unknown": "آزمایش‌نشده",
}


class ProxyMonitor:
    def __init__(self, db) -> None:
        self.db = db
        self._task: asyncio.Task | None = None
        # proxy_id -> last known status; seeds on first pass (no alert storm)
        self._last: Dict[int, str] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="proxy-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _interval_minutes(self) -> int:
        from ..core.settings_service import get_runtime

        try:
            return int(await get_runtime(self.db, "proxy_monitor_interval") or 0)
        except Exception:
            return 0

    async def _loop(self) -> None:
        while True:
            try:
                minutes = await self._interval_minutes()
                if minutes <= 0:
                    # disabled: clear memory so re-enabling doesn't replay old diffs
                    if self._last:
                        self._last.clear()
                    await asyncio.sleep(60)
                    continue
                await asyncio.sleep(minutes * 60)
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("proxy monitor pass failed: %s", exc)
                await asyncio.sleep(120)

    async def _run_once(self) -> None:
        from ..core.state import state
        from ..services import proxy_service

        repo = ProxyRepo(self.db)
        rows = await proxy_service.speed_test_all(repo)
        if not rows:
            return
        proxy_service.selector.invalidate()

        changes = []
        current: Dict[int, str] = {}
        for row in rows:
            current[row["id"]] = row["status"]
            prev = self._last.get(row["id"])
            if prev is not None and prev != row["status"]:
                arrow = f"{STATUS_LABELS.get(prev, prev)} → {STATUS_LABELS.get(row['status'], row['status'])}"
                changes.append(f"• {row['label'] or row['host']} ({row['host']}:{row['port']}): {arrow}")
        self._last = current

        if not changes:
            return
        enabled = sum(1 for r in rows if r["status"] in ("ok", "degraded") and r["enabled"])
        text = "<b>🛰 گزارش سلامت پراکسی‌ها</b>\n" + "\n".join(changes) + f"\n\nپراکسی‌های قابل استفاده: {enabled}"
        try:
            from .notify import notify_admins

            await notify_admins(text)
        except Exception as exc:
            log.warning("proxy monitor notify failed: %s", exc)
