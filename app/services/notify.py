"""Telegram notifications to configured admin chat ids (best-effort, never blocks)."""
from __future__ import annotations

import logging

import httpx

from ..core.config import get_settings

log = logging.getLogger("tgdrive.notify")


async def notify_admins(text: str) -> None:
    """Send to every TGDRIVE_BOT_ADMIN_IDS via the first ready bot token.

    # ponytail: sequential sends + first-bot-only; fan-out if multi-bot alerts matter
    """
    s = get_settings()
    ids = s.bot_admin_id_set
    if not ids or not state_bots():
        return
    token = await _first_ready_token()
    if not token:
        return
    base = s.bot_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as http:
        for chat_id in ids:
            try:
                await http.post(
                    f"{base}/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"},
                )
            except Exception as exc:
                log.warning("admin notify failed for %s: %s", chat_id, exc)


def state_bots():
    from .state import state

    return state.bots


async def _first_ready_token() -> str | None:
    from .security import decrypt_str
    from .state import state
    from .models import BotRepo

    if state.db is None:
        return None
    rows = await BotRepo(state.db).list()
    for r in rows:
        if r["enabled"] and r["status"] == "ready":
            row = await BotRepo(state.db).get(r["id"])
            if row:
                return decrypt_str(row["token_enc"])
    return None
