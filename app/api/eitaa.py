"""Eitaayar token management: add, list, toggle, delete, test (send-only API)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.models import EitaaAccountRepo
from ..core.security import encrypt_str
from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/eitaa", tags=["eitaa"])


class EitaaIn(BaseModel):
    token: str
    label: str = ""
    chat_id: str = ""


@router.get("")
async def list_eitaa(_: str = Depends(get_current_admin), db=Depends(get_db)):
    return {"items": await EitaaAccountRepo(db).list()}


@router.post("")
async def add_eitaa(body: EitaaIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    token = body.token.strip()
    if len(token) < 10:
        raise HTTPException(status_code=400, detail="invalid eitaayar token")
    repo = EitaaAccountRepo(db)
    eitaa_id = await repo.create(
        label=body.label or f"eitaa-{token[:4]}…",
        token_enc=encrypt_str(token),
        chat_id=body.chat_id.strip().lstrip("@"),
    )
    await repo.set_status(eitaa_id, "ready")
    if state.manager:
        await state.manager.refresh_one_eitaa(eitaa_id)
    await db.audit(admin, "eitaa.add", target=str(eitaa_id))
    return {"id": eitaa_id}


@router.post("/{eitaa_id}/toggle")
async def toggle_eitaa(eitaa_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = EitaaAccountRepo(db)
    row = await repo.get(eitaa_id)
    if not row:
        raise HTTPException(status_code=404, detail="eitaa account not found")
    enabled = not bool(row["enabled"])
    await repo.set_enabled(eitaa_id, enabled)
    if state.manager:
        if enabled:
            await state.manager.refresh_one_eitaa(eitaa_id)
        else:
            await state.manager.drop("eit:", eitaa_id)
    return {"enabled": enabled}


@router.delete("/{eitaa_id}")
async def delete_eitaa(eitaa_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = EitaaAccountRepo(db)
    if not await repo.get(eitaa_id):
        raise HTTPException(status_code=404, detail="eitaa account not found")
    await repo.delete(eitaa_id)
    if state.manager:
        await state.manager.drop("eit:", eitaa_id)
    await db.audit(admin, "eitaa.delete", target=str(eitaa_id))
    return {"ok": True}


@router.post("/{eitaa_id}/test")
async def test_eitaa(eitaa_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Send a tiny real file to prove the token + chat work."""
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    key = f"eit:{eitaa_id}"
    import os
    import tempfile

    from ..core.config import get_settings

    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("tgdrive probe")
        async with await state.manager.acquire_key(key) as backend:
            result = await backend.send_document(
                getattr(backend, "storage_chat", ""), path, f"probe_{eitaa_id}.txt", "text/plain"
            )
        state.manager.release_stats(key)
        return {"ok": True, "message_id": result["message_id"], "account_id": eitaa_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "account_id": eitaa_id}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
