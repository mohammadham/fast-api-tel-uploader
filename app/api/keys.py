"""API key management: create (value shown once), list, revoke, update quotas."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.models import ApiKeyRepo
from ..core.rate_limit import quota
from ..core.security import generate_api_key
from ..core.settings_service import get_runtime
from ..core.state import get_db
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])


class KeyIn(BaseModel):
    name: str
    scopes: str = "read,write"
    rpm: Optional[int] = None
    daily_quota_gb: Optional[float] = None
    expires_in_days: Optional[int] = None
    backend: str = ""  # '' = follow system default; 'telegram' | 'eitaa'


@router.get("")
async def list_keys(_: str = Depends(get_current_admin), db=Depends(get_db)):
    rows = await ApiKeyRepo(db).list()
    s = get_settings()
    dflt_rpm = int(await get_runtime(db, "default_key_rpm") or s.default_key_rpm)
    for r in rows:
        k = f"key:{r['id']}"
        quota.reset_if_new_day(k)
        r["used_bytes_today"] = quota.used(k)
        r["rpm"] = r["rpm"] or dflt_rpm
    return {"items": rows}


@router.post("")
async def create_key(body: KeyIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    s = get_settings()
    dflt_rpm = int(await get_runtime(db, "default_key_rpm") or s.default_key_rpm)
    dflt_quota = int(await get_runtime(db, "default_key_daily_quota") or s.default_key_daily_quota)
    raw = generate_api_key()
    expires_at = (
        __import__("time").time() + body.expires_in_days * 86400 if body.expires_in_days else None
    )
    backend = body.backend.strip().lower()
    if backend not in ("", "telegram", "eitaa"):
        raise HTTPException(status_code=400, detail="backend must be '', 'telegram' or 'eitaa'")
    info = await ApiKeyRepo(db).create(
        name=body.name.strip() or "unnamed",
        raw_key=raw,
        scopes=body.scopes,
        rpm=body.rpm or dflt_rpm,
        daily_quota_bytes=int((body.daily_quota_gb or 0) * (1024**3)) or dflt_quota,
        expires_at=expires_at,
        backend=backend,
    )
    await db.audit(admin, "apikey.create", target=str(info.get("prefix", "")), details=body.name[:100])
    return info  # includes raw `key` — shown exactly once


@router.delete("/{key_id}")
async def revoke_key(key_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    n = await ApiKeyRepo(db).revoke(key_id)
    if not n:
        raise HTTPException(status_code=404, detail="key not found")
    await db.audit(admin, "apikey.revoke", target=str(key_id))
    return {"ok": True}


class KeyPatch(BaseModel):
    rpm: Optional[int] = None
    daily_quota_gb: Optional[float] = None
    scopes: Optional[str] = None
    backend: Optional[str] = None  # '' = follow default


@router.patch("/{key_id}")
async def update_key(key_id: int, body: KeyPatch, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    backend = body.backend.strip().lower() if body.backend is not None else None
    if backend not in (None, "", "telegram", "eitaa"):
        raise HTTPException(status_code=400, detail="backend must be '', 'telegram' or 'eitaa'")
    await ApiKeyRepo(db).update(
        key_id,
        rpm=body.rpm,
        daily_quota_bytes=int(body.daily_quota_gb * (1024**3)) if body.daily_quota_gb is not None else None,
        scopes=body.scopes,
        backend=backend,
    )
    return {"ok": True}
