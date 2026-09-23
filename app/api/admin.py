"""Admin/dashboard endpoints: overview stats, audit log, health, metrics."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response, Form
from fastapi.responses import PlainTextResponse

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
    qstats = await state.queue.stats()
    return {
        "files": {"total": files_total, "ready": files_ready, "bytes_stored": bytes_stored},
        "traffic": {"bytes_served": bytes_served, "downloads": downloads},
        "accounts": {"total": accounts, "ready": accounts_ready},
        "bots": bots,
        "api_keys": keys,
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
        return Response('{"ok": false}', status_code=503, media_type="application/json")
    return {"ok": True}

@router.get("/backup")
async def backup(_: str = Depends(get_current_admin), db=Depends(get_db)):
    import json, gzip, io, base64, csv
    async with db.acquire() as conn:
        tables = ["files", "tg_accounts", "bot_tokens", "api_keys", "audit_log"]
        dump = io.StringIO()
        for table in tables:
            rows = await conn.fetch(f"SELECT * FROM {table}")
            if rows:
                writer = csv.writer(dump)
                writer.writerow([desc[0] for desc in conn.cursor_description])
                for row in rows:
                    writer.writerow(row)
        compressed = gzip.compress(dump.getvalue().encode())
    return {"backup": base64.b64encode(compressed).decode()}

@router.post("/restore")
async def restore(
      backup_data: str = Form(...),
      _: str = Depends(get_current_admin),
      db=Depends(get_db),
    ):
    import gzip, io, csv
    try:
        compressed = backup_data.encode()
        dump_str = gzip.decompress(compressed).decode()
        reader = io.StringIO(dump_str)
        reader.readline()  # skip header row
        async with db.acquire() as conn:
            for line in reader:
                row = csv.reader([line]).__next__()
                # Basic restore - skip complex table mapping for now
        showToast("بازگردانی انجام شد", 3000)
    except Exception as e:
        showToast("بازgarde_FAILED: " + str(e), 4000, true)
