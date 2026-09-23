"""Telegram account management: phone-login flow + CRUD + health actions.

The interactive login (phone → code → optional 2FA password) is handled as a
small state machine keyed by login_id so the panel can drive it via REST.
In fake mode the flow is auto-completed without a real code.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.models import AccountRepo, new_id
from ..core.state import get_db, state
from .deps import get_client_ip, get_current_admin

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

# login_id → {"client": TelegramClient, "phone": str, "ts": float}
_logins: Dict[str, Dict[str, Any]] = {}


class StartIn(BaseModel):
    phone: str
    label: str = ""


class CompleteIn(BaseModel):
    login_id: str
    code: str = ""
    password: str = ""


class AccountOut(BaseModel):
    label: str = ""
    storage_chat_id: str = "me"


@router.get("")
async def list_accounts(_: str = Depends(get_current_admin), db=Depends(get_db)):
    rows = await AccountRepo(db).list()
    manager = state.manager
    health = {h["key"]: h for h in (manager.health() if manager else [])}
    out = []
    for r in rows:
        h = health.get(f"acc:{r['id']}", {})
        out.append({**r, "pool": h})
    return {"items": out}


@router.post("/login/start")
async def login_start(body: StartIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    s = get_settings()
    login_id = new_id("login")
    if s.fake_tg:
        # auto-complete in fake mode
        session_enc = _fake_session(body.phone)
        acc_id = await AccountRepo(db).create(
            label=body.label or f"fake-{body.phone[-4:]}",
            phone=body.phone,
            session_enc=session_enc,
            is_premium=False,
        )
        await AccountRepo(db).set_session(acc_id, session_enc, "ready")
        if state.manager:
            await state.manager.refresh_one_account(acc_id)
        return {"login_id": "", "status": "ready", "account_id": acc_id}

    if not s.tg_api_id or not s.tg_api_hash:
        raise HTTPException(status_code=400, detail="TGDRIVE_TG_API_ID / TGDRIVE_TG_API_HASH not configured")
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), s.tg_api_id, s.tg_api_hash)
        await asyncio.to_thread(client.connect)
        sent = await asyncio.to_thread(
            client.send_code_request, body.phone.strip()
        )
    except Exception as exc:
        if type(exc).__name__ == "PhoneNumberInvalidError":
            raise HTTPException(status_code=400, detail="invalid phone number format (use +countrycode...)")
        if type(exc).__name__ == "PhoneNumberBannedError":
            raise HTTPException(status_code=400, detail="phone number is banned by telegram")
        if type(exc).__name__ == "FloodWaitError":
            raise HTTPException(status_code=429, detail=f"telegram flood: retry after {getattr(exc, 'seconds', 60)}s")
        raise HTTPException(status_code=400, detail=f"telegram error: {exc}")
    _logins[login_id] = {"client": client, "phone": body.phone.strip(), "ts": time.time(), "label": body.label}
    return {"login_id": login_id, "status": "code_sent"}


@router.post("/login/complete")
async def login_complete(body: CompleteIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    entry = _logins.get(body.login_id)
    if not entry:
        raise HTTPException(status_code=404, detail="login session not found")
    client = entry["client"]
    try:
        if body.code:
            try:
                await asyncio.to_thread(
                    client.sign_in, entry["phone"], body.code.strip()
                )
            except Exception as exc:
                name = type(exc).__name__
                if name == "SessionPasswordNeededError":
                    return {"login_id": body.login_id, "status": "password_needed"}
                if name == "PhoneCodeExpiredError":
                    _logins.pop(body.login_id, None)
                    try:
                        await asyncio.to_thread(client.disconnect)
                    except Exception:
                        pass
                    raise HTTPException(status_code=400, detail="code expired — please start the login again")
                if name == "PhoneCodeInvalidError":
                    raise HTTPException(status_code=400, detail="invalid code")
                raise
        elif body.password:
            try:
                await asyncio.to_thread(client.sign_in, password=body.password)
            except Exception as exc:
                if type(exc).__name__ == "PasswordHashInvalidError":
                    raise HTTPException(status_code=400, detail="invalid 2FA password")
                raise
        else:
            raise HTTPException(status_code=400, detail="code or password required")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"telegram error: {exc}")

    session_string = client.session.save()  # StringSession
    try:
        await asyncio.to_thread(client.disconnect)
    except Exception:
        pass
    from ..core.security import encrypt_str

    repo = AccountRepo(db)
    acc_id = await repo.create(
        label=entry.get("label") or f"acc-{entry['phone'][-4:]}",
        phone=entry["phone"],
        session_enc=encrypt_str(str(session_string)),
        is_premium=bool(getattr(client, "is_premium", False)),
    )
    await repo.set_session(acc_id, encrypt_str(str(session_string)), "ready")
    _logins.pop(body.login_id, None)
    if state.manager:
        await state.manager.refresh_one_account(acc_id)
    return {"login_id": "", "status": "ready", "account_id": acc_id}


def _fake_session(phone: str) -> str:
    return f"fake-session::{phone}::{secrets.token_hex(8)}"


@router.patch("/{account_id}")
async def update_account(account_id: int, body: AccountOut, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = AccountRepo(db)
    row = await repo.get(account_id)
    if not row:
        raise HTTPException(status_code=404, detail="account not found")
    if body.label:
        await db.execute("UPDATE tg_accounts SET label=? WHERE id=?", (body.label, account_id))
    if body.storage_chat_id:
        await db.execute(
            "UPDATE tg_accounts SET storage_chat_id=? WHERE id=?", (body.storage_chat_id, account_id)
        )
    if state.manager:
        await state.manager.drop("acc:", account_id)
        await state.manager.refresh_one_account(account_id)
    return {"ok": True}


@router.post("/{account_id}/toggle")
async def toggle_account(account_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = AccountRepo(db)
    row = await repo.get(account_id)
    if not row:
        raise HTTPException(status_code=404, detail="account not found")
    new_enabled = not bool(row["enabled"])
    await repo.set_enabled(account_id, new_enabled)
    if state.manager:
        if new_enabled:
            await state.manager.refresh_one_account(account_id)
        else:
            await state.manager.drop("acc:", account_id)
    return {"enabled": new_enabled}


@router.delete("/{account_id}")
async def delete_account(account_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = AccountRepo(db)
    if not await repo.get(account_id):
        raise HTTPException(status_code=404, detail="account not found")
    await repo.delete(account_id)
    if state.manager:
        await state.manager.drop("acc:", account_id)
    await db.audit(admin, "account.delete", target=str(account_id))
    return {"ok": True}


@router.post("/{account_id}/test")
async def test_account(account_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Directly test THIS account's backend (not a random pool member)."""
    repo = AccountRepo(db)
    row = await repo.get(account_id)
    if not row:
        raise HTTPException(status_code=404, detail="account not found")
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    key = f"acc:{account_id}"
    try:
        async with await state.manager.acquire_key(key) as backend:
            me = await backend.get_me() if hasattr(backend, "get_me") else {"id": backend.id}
        if state.manager._available(key):
            # successful probe resets transient error counter
            state.manager.release_stats(key)
        return {"ok": True, "me": str(me), "account_id": account_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "account_id": account_id}


@router.post("/{account_id}/reset")
async def reset_account_state(account_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Clear flood/circuit/error state and reconnect (admin action after fixing an account)."""
    repo = AccountRepo(db)
    if not await repo.get(account_id):
        raise HTTPException(status_code=404, detail="account not found")
    if state.manager:
        await state.manager.drop("acc:", account_id)
        await state.manager.refresh_one_account(account_id)
    await db.audit(admin, "account.reset", target=str(account_id))
    return {"ok": True, "reconnected": True}


# cleanup old login attempts periodically (simple, on access)
def _gc_logins() -> None:
    cutoff = time.time() - 600
    for lid, entry in list(_logins.items()):
        if entry["ts"] < cutoff:
            entry["client"] and _safe_disconnect(entry["client"])
            _logins.pop(lid, None)


def _safe_disconnect(client) -> None:
    try:
        asyncio.get_event_loop().create_task(asyncio.to_thread(client.disconnect))
    except Exception:
        pass
