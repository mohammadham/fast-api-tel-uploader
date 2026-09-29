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

from ..core.config import Settings, get_settings
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


def _effective_tg_settings() -> "Settings":
    """Env settings overlaid with the panel's runtime DB overrides.

    Mirrors TGManager._settings(): the panel saves tg_api_id/tg_api_hash/fake_tg
    into the settings table, so "real mode" configured from the UI must be
    honored here too — reading bare get_settings() made login_start think it
    was still in fake-TG (or use empty credentials) after the user switched
    modes from the panel, and the code request was never sent.
    """
    s = get_settings()
    try:
        from ..core.settings_service import runtime_settings

        cache = runtime_settings()._cache
        if cache:
            if "fake_tg" in cache:
                s.fake_tg = bool(int(cache["fake_tg"] or 0))
            if cache.get("tg_api_id"):
                s.tg_api_id = int(cache["tg_api_id"])
            if cache.get("tg_api_hash"):
                s.tg_api_hash = str(cache["tg_api_hash"])
    except Exception:
        pass
    return s


@router.post("/login/start")
async def login_start(body: StartIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    s = _effective_tg_settings()
    login_id = new_id("login")
    await gc_logins()  # drop stale pending logins + disconnect their clients
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
        raise HTTPException(status_code=400, detail="API ID / API Hash تلگرام تنظیم نشده — ابتدا در تب تنظیمات وارد کنید")
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # honor the active proxy (same selector the backends use) — without it
        # send_code_request silently times out in regions where telegram is
        # blocked and the user just sees "no code arrived"
        proxy = None
        try:
            from ..core.security import decrypt_str
            from ..services.proxy_service import get_active_proxy, build_telethon_proxy

            prow = await get_active_proxy(db)
            if prow:
                prow["_password_plain"] = decrypt_str(prow["password_enc"]) if prow.get("password_enc") else ""
                proxy = build_telethon_proxy(prow)
        except Exception:
            proxy = None
        client = TelegramClient(StringSession(), s.tg_api_id, s.tg_api_hash, proxy=proxy)
        # Telethon's API is async — these MUST be awaited on the event loop.
        # The old asyncio.to_thread(...) wrapping silently created the coroutine
        # without ever running it: login_start returned "code_sent" instantly and
        # no SMS/telegram code ever went out.
        await client.connect()
        sent = await client.send_code_request(body.phone.strip())
    except Exception as exc:
        if type(exc).__name__ == "PhoneNumberInvalidError":
            raise HTTPException(status_code=400, detail="invalid phone number format (use +countrycode...)")
        if type(exc).__name__ == "PhoneNumberBannedError":
            raise HTTPException(status_code=400, detail="phone number is banned by telegram")
        if type(exc).__name__ == "FloodWaitError":
            raise HTTPException(status_code=429, detail=f"telegram flood: retry after {getattr(exc, 'seconds', 60)}s")
        raise HTTPException(status_code=400, detail=f"telegram error: {exc}")
    _logins[login_id] = {
        "client": client,
        "phone": body.phone.strip(),
        "ts": time.time(),
        "label": body.label,
        "pw_attempts": 0,
    }
    return {"login_id": login_id, "status": "code_sent"}


class ResendIn(BaseModel):
    login_id: str


@router.post("/login/resend")
async def login_resend(body: ResendIn, admin: str = Depends(get_current_admin)):
    """Re-send the login code for a PENDING login (same client/session), capped.

    The panel only offers this after its 2-minute cooldown; the server keeps its
    own counter so the cap holds regardless of the client.
    """
    entry = _logins.get(body.login_id)
    if not entry:
        raise HTTPException(status_code=404, detail="login session not found")
    if entry.get("code_resends", 0) >= MAX_CODE_RESENDS:
        raise HTTPException(status_code=429, detail="حداکثر ۳ بار ارسال مجدد کد مجاز است — دوباره تلاش کنید بعداً")
    client = entry["client"]
    try:
        if not client.is_connected():
            await client.connect()
        await client.send_code_request(entry["phone"])
    except Exception as exc:
        if type(exc).__name__ == "FloodWaitError":
            raise HTTPException(status_code=429, detail=f"تلگرام محدودیت زمانی گذاشته — بعد از {getattr(exc, 'seconds', 60)} ثانیه تلاش کنید")
        raise HTTPException(status_code=400, detail=f"telegram error: {exc}")
    entry["code_resends"] = entry.get("code_resends", 0) + 1
    entry["ts"] = time.time()
    return {"ok": True, "resends_left": MAX_CODE_RESENDS - entry["code_resends"]}


@router.post("/login/complete")
async def login_complete(body: CompleteIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    entry = _logins.get(body.login_id)
    if not entry:
        raise HTTPException(status_code=404, detail="login session not found")
    client = entry["client"]
    if body.password and not body.code:
        # 2FA step: cap attempts per login_id so brute-forcing the cloud
        # password through this API is not possible
        if entry.get("pw_attempts", 0) >= MAX_PASSWORD_ATTEMPTS:
            await _drop_login(body.login_id, client)
            raise HTTPException(status_code=429, detail="تعداد تلاش‌های رمز 2FA بیش از حد مجاز — لاگین را از ابتدا شروع کنید")
    try:
        if body.code:
            try:
                # Telethon's API is async — await directly (same fix as
                # login_start; to_thread() here never ran the coroutine).
                await client.sign_in(entry["phone"], body.code.strip())
            except Exception as exc:
                name = type(exc).__name__
                if name == "SessionPasswordNeededError":
                    entry["awaiting_password"] = True
                    return {"login_id": body.login_id, "status": "password_needed"}
                if name == "PhoneCodeExpiredError":
                    _logins.pop(body.login_id, None)
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    raise HTTPException(status_code=400, detail="کد منقضی شده — لطفاً لاگین را از ابتدا شروع کنید")
                if name == "PhoneCodeInvalidError":
                    raise HTTPException(status_code=400, detail="کد وارد شده اشتباه است")
                raise
        elif body.password:
            try:
                await client.sign_in(password=body.password)
            except Exception as exc:
                if type(exc).__name__ == "PasswordHashInvalidError":
                    entry["pw_attempts"] = entry.get("pw_attempts", 0) + 1
                    left = MAX_PASSWORD_ATTEMPTS - entry["pw_attempts"]
                    if left <= 0:
                        await _drop_login(body.login_id, client)
                        raise HTTPException(status_code=429, detail="تعداد تلاش‌های رمز 2FA بیش از حد مجاز — لاگین را از ابتدا شروع کنید")
                    raise HTTPException(status_code=400, detail=f"رمز 2FA اشتباه است — {left} تلاش باقی مانده")
                raise
        else:
            raise HTTPException(status_code=400, detail="کد یا رمز لازم است")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"telegram error: {exc}")

    # Telethon's async sign_in updates the session (auth key) server-side too;
    # persist the post-login StringSession ("1:B..." form) so the account can
    # reconnect later without this login flow.
    session_string = str(client.session.save())
    # a session that is not actually authorized would just fail on every future
    # backend start — fail the login loudly instead of storing a dead account
    if not getattr(client, "is_user_authorized", lambda: True)():
        await _drop_login(body.login_id, client)
        raise HTTPException(status_code=400, detail="ورود کامل نشد (سشن مجاز نیست) — دوباره تلاش کنید")
    try:
        await client.disconnect()
    except Exception:
        pass
    from ..core.security import encrypt_str

    repo = AccountRepo(db)
    acc_id = await repo.create(
        label=entry.get("label") or f"acc-{entry['phone'][-4:]}",
        phone=entry["phone"],
        session_enc=encrypt_str(session_string),
        is_premium=bool(getattr(client, "is_premium", False)),
    )
    await repo.set_session(acc_id, encrypt_str(session_string), "ready")
    _logins.pop(body.login_id, None)
    if state.manager:
        # best-effort: the account row is already persisted — a failed initial
        # connect (proxy hiccup, session not ready yet) must NOT turn the
        # successful login into a 500 the panel misreads as "login failed"
        try:
            await state.manager.refresh_one_account(acc_id)
        except Exception as exc:
            from ..core.obs import log as _log

            _log.warning("post-login refresh for account %s failed (account saved): %s", acc_id, exc)
    return {"login_id": "", "status": "ready", "account_id": acc_id}


# ── login flow helpers ──────────────────────────────────────────
MAX_PASSWORD_ATTEMPTS = 3
MAX_CODE_RESENDS = 3
LOGIN_TTL_SECONDS = 600  # pending login lifetime; panel's resend window is shorter


def _fake_session(phone: str) -> str:
    return f"fake-session::{phone}::{secrets.token_hex(8)}"


async def _drop_login(login_id: str, client=None) -> None:
    """Forget a pending login and disconnect its client (best-effort)."""
    entry = _logins.pop(login_id, None)
    c = client or (entry or {}).get("client")
    if c is not None:
        try:
            await c.disconnect()
        except Exception:
            pass


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
        from .admin import _invalidate_storage_channels_cache
        _invalidate_storage_channels_cache()
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


async def gc_logins() -> None:
    """Drop stale login attempts (called from login_start) — disconnects clients."""
    cutoff = time.time() - LOGIN_TTL_SECONDS
    for lid in [lid for lid, e in _logins.items() if e["ts"] < cutoff]:
        await _drop_login(lid)
