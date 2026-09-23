"""Bot token management: add (validated via getMe), enable/disable, delete."""
from __future__ import annotations

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
    return {"items": await BotRepo(db).list()}


@router.post("")
async def add_bot(body: BotIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    token = body.token.strip()
    if token.startswith("Bot "):
        token = token[4:]
    if len(token) < 30 or ":" not in token:
        raise HTTPException(status_code=400, detail="invalid bot token format")
    s = get_settings()
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
