"""Admin/dashboard endpoints: overview stats, audit log, health, metrics,
runtime settings, setup wizard, node registry, backup/restore."""
from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..core.metrics import metrics
from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/overview")
async def overview(_: str = Depends(get_current_admin), db=Depends(get_db)):
    files_total = await db.scalar("SELECT COUNT(*) FROM files") or 0
    files_ready = await db.scalar("SELECT COUNT(*) FROM files WHERE status='ready'") or 0
    bytes_stored = await db.scalar("SELECT COALESCE(SUM(size),0) FROM files WHERE status='ready'") or 0
    bytes_served = await db.scalar("SELECT COALESCE(SUM(bytes_served),0) FROM files") or 0
    downloads = await db.scalar("SELECT COALESCE(SUM(downloads),0) FROM files") or 0
    accounts = await db.scalar("SELECT COUNT(*) FROM tg_accounts") or 0
    accounts_ready = await db.scalar("SELECT COUNT(*) FROM tg_accounts WHERE status='ready' AND enabled=1") or 0
    bots = await db.scalar("SELECT COUNT(*) FROM bot_tokens") or 0
    keys = await db.scalar("SELECT COUNT(*) FROM api_keys WHERE revoked=0") or 0
    nodes = await db.scalar("SELECT COUNT(*) FROM nodes") or 0
    qstats = await state.queue.stats() if state.queue else {}
    return {
        "files": {"total": files_total, "ready": files_ready, "bytes_stored": bytes_stored},
        "traffic": {"bytes_served": bytes_served, "downloads": downloads},
        "accounts": {"total": accounts, "ready": accounts_ready},
        "bots": bots,
        "api_keys": keys,
        "nodes": nodes,
        "queue": qstats,
        "uptime": round(time.time() - metrics._start, 1),
    }


@router.get("/audit")
async def audit(limit: int = 200, _: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import AuditRepo

    return {"items": await AuditRepo(db).list(min(limit, 1000))}


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus(_: str = Depends(get_current_admin)):
    # gauge refresh from live state
    if state.queue:
        q = await state.queue.stats()
        metrics.set_gauge("queue.pending", q.get("pending", 0))
        metrics.set_gauge("queue.running", q.get("running", 0))
    if state.manager:
        h = state.manager.health()
        metrics.set_gauge("backends.healthy", sum(1 for x in h if x["available"]))
    return metrics.render()


@router.get("/healthz")
async def healthz(db=Depends(get_db)):
    await db.scalar("SELECT 1")
    return {"ok": True}


@router.get("/readyz")
async def readyz(db=Depends(get_db)):
    ok = bool(state.queue) and bool(state.manager)
    if not ok:
        return PlainTextResponse('{"ok": false}', status_code=503, media_type="application/json")
    return {"ok": True}


# ── backup / restore (rewritten: old version used nonexistent db.acquire/cursor_description) ──
_BACKUP_TABLES = ("files", "file_parts", "tg_accounts", "bot_tokens", "eitaa_accounts", "api_keys", "links")


@router.get("/backup")
async def backup(_: str = Depends(get_current_admin), db=Depends(get_db)):
    """Table-rows JSON backup (no encrypted secrets for safety: tokens/sessions excluded)."""
    dump: dict[str, list] = {}
    for table in _BACKUP_TABLES:
        cols = await db.fetch_all(f"PRAGMA table_info({table})") if db.is_sqlite else []
        cols = [c["name"] for c in cols] if cols else []
        rows = await db.fetch_all(f"SELECT * FROM {table}")
        if cols:
            # drop encrypted columns — they are node-specific secrets, not portable
            drop = {"session_enc", "token_enc", "pwd_hash"}
            keep = [c for c in cols if c not in drop]
            dump[table] = [{k: r.get(k) for k in keep} for r in rows]
        else:
            dump[table] = rows
    payload = json.dumps({"ts": time.time(), "tables": dump}).encode()
    return {
        "ts": time.time(),
        "tables": {k: len(v) for k, v in dump.items()},
        "data": base64.b64encode(gzip.compress(payload)).decode(),
    }


class RestoreIn(BaseModel):
    data: str  # base64 gzip JSON from /backup


@router.post("/restore")
async def restore(body: RestoreIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    try:
        payload = json.loads(gzip.decompress(base64.b64decode(body.data)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid backup payload: {exc}")
    tables = payload.get("tables") or {}
    counts: dict[str, int] = {}
    for table, rows in tables.items():
        if table not in _BACKUP_TABLES or not isinstance(rows, list) or not rows:
            continue
        for row in rows:
            cols = list(row.keys())
            ph = ",".join("?" if db.is_sqlite else f":{c}" for c in cols)
            names = ",".join(cols)
            params = tuple(row[c] for c in cols) if db.is_sqlite else {c: row[c] for c in cols}
            try:
                if db.is_sqlite:
                    await db.execute(
                        f"INSERT OR IGNORE INTO {table}({names}) VALUES({ph})", params
                    )
                else:
                    await db.execute(
                        f"INSERT INTO {table}({names}) VALUES({ph}) ON CONFLICT DO NOTHING", params
                    )
            except Exception:
                continue  # skip rows that collide or miss required columns
        counts[table] = len(rows)
    await db.audit(admin, "admin.restore", details=json.dumps(counts)[:300])
    return {"ok": True, "restored": counts}


# ── runtime settings (DB-backed, admin-managed) ──────────────────
@router.get("/settings")
async def get_settings_api(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.settings_service import editable_settings_meta

    return {"items": await editable_settings_meta(db)}


@router.put("/settings")
async def put_settings(
    body: dict,
    admin: str = Depends(get_current_admin),
    db=Depends(get_db),
):
    from ..core.settings_service import save_runtime_settings

    try:
        applied = await save_runtime_settings(db, body, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "applied": applied}


@router.post("/settings/reset")
async def reset_settings(admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.settings_service import reset_runtime_settings

    await reset_runtime_settings(db, actor=admin)
    return {"ok": True}


# ── setup wizard (first-run flag in system_meta) ─────────────────
@router.get("/setup/status")
async def setup_status(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.settings_service import get_meta

    return {
        "initialized": (await get_meta(db, "initialized")) == "1",
        "initialized_at": float(await get_meta(db, "initialized_at") or 0),
    }


class SetupCompleteIn(BaseModel):
    new_password: str = ""
    default_backend: str = ""


@router.post("/setup/complete")
async def setup_complete(
    body: SetupCompleteIn,
    admin: str = Depends(get_current_admin),
    db=Depends(get_db),
):
    from ..core.models import UserRepo
    from ..core.settings_service import get_meta, set_meta

    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(status_code=400, detail="password too short (min 6)")
        await UserRepo(db).set_password(admin, body.new_password)
    if body.default_backend:
        be = body.default_backend.strip().lower()
        if be not in ("telegram", "eitaa"):
            raise HTTPException(status_code=400, detail="backend must be 'telegram' or 'eitaa'")
        from ..core.settings_service import save_runtime_settings

        await save_runtime_settings(db, {"default_backend": be}, actor=admin)
    if (await get_meta(db, "initialized")) != "1":
        await set_meta(db, "initialized", "1")
        await set_meta(db, "initialized_at", str(time.time()))
    await db.audit(admin, "setup.complete")
    return {"ok": True, "initialized": True}


# ── node registry (multi-server mode) ────────────────────────────
@router.get("/nodes")
async def nodes(_: str = Depends(get_current_admin), db=Depends(get_db)):
    return {
        "items": await db.fetch_all("SELECT * FROM nodes ORDER BY last_heartbeat DESC"),
        "node_id": state.node_id,
    }
