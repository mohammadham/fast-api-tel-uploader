"""Bot token management: add (validated via getMe), enable/disable, delete.

Per-bot ops: a direct health probe (/test) and a 24h handled-files counter
fed from the queue jobs table (jobs record which backend handled them).
"""
from __future__ import annotations

import os
import tempfile
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.models import BotRepo, new_id
from ..core.security import encrypt_str
from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/bots", tags=["bots"])


class BotIn(BaseModel):
    token: str
    label: str = ""


@router.get("")
async def list_bots(_: str = Depends(get_current_admin), db=Depends(get_db)):
    rows = await BotRepo(db).list()
    # handled_24h: queue jobs this backend handled in the last 24h. The queue
    # writes the handling backend key into the job payload on completion.
    since = time.time() - 86400
    handled: dict = {}
    try:
        rows_jobs = await db.fetch_all(
            "SELECT payload FROM jobs WHERE status='done' AND finished_at >= ?",
            (since,),
        )
        import json as _json

        for r in rows_jobs:
            try:
                k = (_json.loads(r["payload"] if isinstance(r["payload"], str) else "{}") or {}).get("handled_by", "")
            except Exception:
                continue
            if k:
                handled[k] = handled.get(k, 0) + 1
    except Exception:
        handled = {}
    return {"items": [{**r, "handled_24h": handled.get(f"bot:{r['id']}", 0)} for r in rows]}


@router.post("")
async def add_bot(body: BotIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    token = body.token.strip()
    if token.startswith("Bot "):
        token = token[4:]
    if len(token) < 30 or ":" not in token:
        raise HTTPException(status_code=400, detail="invalid bot token format")
    s = get_settings()
    # fake-TG mode: no real Bot API — validate shape only and register the bot
    # locally (mirrors how TGManager overlays env fake_tg with the runtime DB
    # override). Without this, adding a bot from the panel always fails with
    # getMe in dev/test setups.
    fake_mode = bool(s.fake_tg)
    try:
        from ..core.settings_service import runtime_settings

        cache = runtime_settings()._cache
        if cache and "fake_tg" in cache:
            fake_mode = bool(int(cache["fake_tg"] or 0))
    except Exception:
        pass
    if fake_mode:
        bot_id = await BotRepo(db).create(
            label=body.label or "@fake_bot",
            token_enc=encrypt_str(token),
        )
        await BotRepo(db).set_status(bot_id, "ready")
        if state.bots:
            state.bots.start_one(bot_id)
        if state.manager:
            try:
                await state.manager.refresh_one_bot(bot_id)
            except Exception:
                pass
        await db.audit(admin, "bot.add", target=str(bot_id), details="@fake_bot (fake-tg)")
        return {"id": bot_id, "username": "fake_bot"}
    base = s.bot_api_base.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(f"{base}/bot{token}/getMe")
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot reach Bot API: {exc}")
    if not data.get("ok"):
        raise HTTPException(status_code=400, detail="token rejected by telegram (getMe failed)")

    repo = BotRepo(db)
    bot_id = await repo.create(
        label=body.label or f"@{data['result'].get('username', 'bot')}",
        token_enc=encrypt_str(token),
    )
    await repo.set_status(bot_id, "ready")
    if state.bots:
        state.bots.start_one(bot_id)
    if state.manager:
        try:
            await state.manager.refresh_one_bot(bot_id)
        except Exception:
            pass
    await db.audit(admin, "bot.add", target=str(bot_id), details=f"@{data['result'].get('username', '')}")
    return {"id": bot_id, "username": data["result"].get("username")}


@router.post("/{bot_id}/toggle")
async def toggle_bot(bot_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = BotRepo(db)
    row = await repo.get(bot_id)
    if not row:
        raise HTTPException(status_code=404, detail="bot not found")
    enabled = not bool(row["enabled"])
    await repo.set_enabled(bot_id, enabled)
    if state.bots:
        if enabled:
            state.bots.start_one(bot_id)
        else:
            task = state.bots._tasks.pop(bot_id, None)
            if task:
                task.cancel()
    return {"enabled": enabled}


@router.post("/{bot_id}/test")
async def test_bot(bot_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Directly probe THIS bot's backend: send a tiny document to Saved Messages
    (or the system default chat when set) and report ok/error."""
    repo = BotRepo(db)
    row = await repo.get(bot_id)
    if not row:
        raise HTTPException(status_code=404, detail="bot not found")
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    key = f"bot:{bot_id}"
    s = get_settings()
    try:
        from ..core.settings_service import runtime_settings

        cache = runtime_settings()._cache
        chat = (cache.get("tg_storage_chat") or s.tg_storage_chat or "me").strip() or "me"
    except Exception:
        chat = (s.tg_storage_chat or "me").strip() or "me"
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("tgdrive bot probe")
        try:
            async with await state.manager.acquire_key(key) as backend:
                result = await backend.send_document(chat, path, "probe_bot.txt", "text/plain", caption="tgdrive-probe")
            if state.manager._available(key):
                # successful probe resets transient error counter
                state.manager.release_stats(key)
            await repo.set_status(bot_id, "ready", "")
            await db.audit(admin, "bot.test", target=str(bot_id), details=f"{key} → {chat}")
            return {"ok": True, "message_id": result["message_id"], "chat": chat, "bot_id": bot_id}
        except Exception as exc:
            err = str(exc)[:280]
            await repo.set_status(bot_id, "error", err)
            await db.audit(admin, "bot.test", target=str(bot_id), details=f"FAIL {key}: {err}")
            return {"ok": False, "error": err, "bot_id": bot_id}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@router.delete("/{bot_id}")
async def delete_bot(bot_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = BotRepo(db)
    if not await repo.get(bot_id):
        raise HTTPException(status_code=404, detail="bot not found")
    if state.bots:
        task = state.bots._tasks.pop(bot_id, None)
        if task:
            task.cancel()
    await repo.delete(bot_id)
    await db.audit(admin, "bot.delete", target=str(bot_id))
    return {"ok": True}
