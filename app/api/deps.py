"""Shared FastAPI dependencies: JWT auth, API-key auth, rate limiting, quotas."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from ..core.config import get_settings
from ..core.state import get_db
from ..core.models import ApiKeyRepo
from ..core.rate_limit import limiter, quota
from ..core.security import decode_token


async def get_current_admin(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Panel JWT bearer auth."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    payload = decode_token(authorization.split(" ", 1)[1].strip(), "access")
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return payload["sub"]


async def get_api_key(
    request: Request,
    db: Database = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    """API-key auth for programmatic clients + rate limit + quota check."""
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    if not raw:
        raise HTTPException(status_code=401, detail="missing API key")

    from ..core.security import key_hash, decode_token

    # Panel JWT also accepted (admin identity = implicit full-scope key).
    payload = decode_token(raw, "access")
    if payload:
        return {
            "id": f"jwt:{payload['sub']}",
            "rpm": 600,
            "daily_quota_bytes": 0,
            "scopes": "read,write,admin",
            "backend": "",
        }

    repo = ApiKeyRepo(db)
    row = await repo.find_by_raw(raw)
    if not row:
        raise HTTPException(status_code=401, detail="invalid API key")

    settings = get_settings()
    # rate limit (token bucket per key; burst = one minute's worth)
    if not await limiter.allow(f"key:{row['id']}", capacity=max(1, row["rpm"]), per_minute=row["rpm"]):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    # daily quota (bytes are added by transfer endpoints after completion)
    quota.reset_if_new_day(f"key:{row['id']}")
    if row["daily_quota_bytes"] and quota.used(f"key:{row['id']}") > row["daily_quota_bytes"]:
        raise HTTPException(status_code=429, detail="daily quota exceeded")

    await repo.touch(row["id"])
    return row


async def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def require_scope(api_key_row: dict, scope: str) -> None:
    scopes = {s.strip() for s in (api_key_row["scopes"] or "").split(",") if s.strip()}
    if scope not in scopes and "admin" not in scopes:
        raise HTTPException(status_code=403, detail=f"missing scope: {scope}")


async def get_admin_or_key(
    request: Request,
    db=Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Accept panel JWT *or* an API key (read scope) — for list-style endpoints."""
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
        payload = decode_token(raw, "access")
        if payload:
            return {"type": "admin", "username": payload["sub"]}
        row = await ApiKeyRepo(db).find_by_raw(raw)
        if row:
            require_scope(row, "read")
            settings = get_settings()
            if not await limiter.allow(f"key:{row['id']}", capacity=max(1, row["rpm"]), per_minute=row["rpm"]):
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            await ApiKeyRepo(db).touch(row["id"])
            return {"type": "key", "row": row}
    raise HTTPException(status_code=401, detail="missing or invalid credentials")
