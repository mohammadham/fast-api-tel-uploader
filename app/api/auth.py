"""Panel auth: login, refresh, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.models import UserRepo
from ..core.rate_limit import limiter
from ..core.security import create_token, decode_token, verify_password
from ..core.state import get_db
from .deps import get_client_ip, get_current_admin

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/login")
async def login(body: LoginIn, request: Request, db=Depends(get_db)):
    ip = await get_client_ip(request)
    # brute-force guard: 10 login attempts / minute / ip
    if not await limiter.allow(f"login:{ip}", capacity=10, per_minute=10):
        raise HTTPException(status_code=429, detail="too many login attempts")
    row = await UserRepo(db).get(body.username.strip())
    if not row or not verify_password(body.password, row["password_hash"]):
        await db.audit(
            body.username.strip()[:64] or "unknown", "auth.login.fail",
            ip=ip, details="bad credentials",
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    s = get_settings()
    refresh = create_token(body.username, "refresh", s.refresh_ttl_days * 86400)
    await db.audit(body.username.strip()[:64], "auth.login.ok", ip=ip)
    return {
        "access_token": create_token(body.username, "access", s.jwt_ttl_minutes * 60),
        "refresh_token": refresh,
        "expires_in": s.jwt_ttl_minutes * 60,
    }


@router.post("/refresh")
async def refresh(body: RefreshIn, db=Depends(get_db)):
    from ..core.security import refresh_blacklisted

    payload = decode_token(body.refresh_token, "refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    if await refresh_blacklisted(db, body.refresh_token):
        raise HTTPException(status_code=401, detail="refresh token revoked")
    s = get_settings()
    return {
        "access_token": create_token(payload["sub"], "access", s.jwt_ttl_minutes * 60),
        "expires_in": s.jwt_ttl_minutes * 60,
    }


class LogoutIn(BaseModel):
    refresh_token: str = ""


@router.post("/logout")
async def logout(
    body: LogoutIn,
    request: Request,
    username: str = Depends(get_current_admin),
    db=Depends(get_db),
    authorization: str = Header(default=""),
):
    """Blacklist the presented refresh token + audit the event."""
    from ..core.security import blacklist_refresh

    if body.refresh_token:
        await blacklist_refresh(db, body.refresh_token)
    await db.audit(username, "auth.logout", ip=await get_client_ip(request))
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(get_current_admin)):
    return {"username": username}
