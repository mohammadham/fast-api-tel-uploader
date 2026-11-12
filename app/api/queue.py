"""Queue inspection & control: stats, jobs list, pause/resume kinds, purge."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


def _payload_dict(row: dict) -> dict:
    """MySQL returns jobs.payload as JSON text; SQLite also returns text.

    ``dict(raw)`` on a string raises ValueError (the resume endpoint used to
    500 on MySQL).  Parse strings and tolerate malformed legacy payloads.
    """
    raw = (row or {}).get("payload")
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "{}")
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}
    try:
        return dict(raw or {})
    except (TypeError, ValueError):
        return {}


@router.get("/stats")
async def stats(_: str = Depends(get_current_admin), db=Depends(get_db)):
    return await state.queue.stats()


@router.get("/pressure")
async def pressure(_: str = Depends(get_current_admin), db=Depends(get_db)):
    """Upload-pressure snapshot for the admin banner: queue stall behind the
    concurrency gate + backends currently flood-isolated + a plain-language
    summary line. Polled every few seconds while the panel is open."""
    q = state.queue
    if q is None:
        return {"stall": {"stalled": False, "waiting": 0, "threshold": 0, "oldest_waiting_s": 0, "gate_full": False, "gate_capacity": 0, "upload_workers": 0}, "flooded": [], "summary": ""}
    stall = await q._stall_info()
    flooded = q._flooded_backends()
    summary = ""
    if stall["stalled"] and stall["waiting"]:
        summary = (
            f"صف آپلود معطل است: {stall['waiting']} جاب پشت سقف همزمانی "
            f"({stall['oldest_waiting_s']:.0f} ثانیه؛ سقف {stall['gate_capacity']})"
        )
    elif stall["waiting"]:
        summary = f"{stall['waiting']} جاب آپلود منتظر اسلات آزاد (سقف {stall['gate_capacity']})"
    if flooded:
        names = "، ".join(f["key"] for f in flooded)
        summary = (summary + " — " if summary else "") + f"فشار تلگرام: {names} موقتاً از چرخش خارج"
    return {"stall": stall, "flooded": flooded, "summary": summary}


@router.get("/jobs")
async def jobs(limit: int = 100, _: str = Depends(get_current_admin), db=Depends(get_db)):
    """List queue jobs, including the upload payload + the last resumed offset."""
    import time

    from ..core.settings_service import get_runtime
    from ..queue.scheduler import job_size, lane_for

    rows = await state.queue.jobs.recent(min(limit, 500))
    small_max = int(await get_runtime(db, "scheduler_small_max_bytes") or 64 * 1024 * 1024)
    clock = time.time()
    items = []
    for r in rows:
        item = dict(r)
        item["payload"] = _payload_dict(r) if r.get("payload") else None
        # Persisted final metrics survive the in-memory progress map.  They are
        # what makes the panel's "last 10 jobs" useful after a job finishes.
        stored = (item.get("payload") or {}).get("upload_metrics")
        if isinstance(stored, dict):
            item.update(stored)
        item["size_bytes"] = job_size(item)
        item["lane"] = lane_for(item, small_max)
        created = float(r.get("created_at") or clock)
        item["wait_s"] = max(0, clock - created) if r.get("status") in ("pending", "retry") else 0
        live = state.queue._upload_progress.get(r["id"])
        if live:
            item.update(live)
        elif item.get("payload"):
            item["account"] = item["payload"].get("handled_by") or item.get("payload", {}).get("account") or item.get("account") or ""
        items.append(item)
    return {"items": items, "paused": state.queue.paused()}


@router.post("/resume/{job_id}")
async def resume_job(job_id: str, _: str = Depends(get_current_admin), db=Depends(get_db)):
    """Resume a durable upload job from the server's stored upload-session offset.

    Re-enqueues a failed/paused/completed upload job with `next_run_at=0` so a
    worker picks it up immediately, and continues the upload from the offset the
    upload session has already written to (`upload_sessions.offset`). Jobs that
    are already durable (pending/running/retry) and not paused are accepted;
    deleted/trashed/failed jobs are rejected.
    """
    from ..core.models import UploadSessionRepo

    row = await state.queue.jobs.fetch(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job not found")

    # only durable upload jobs may be resumed from offset
    if row["kind"] != "upload":
        raise HTTPException(status_code=409, detail="only upload jobs can be resumed")
    if row["status"] not in ("pending", "running", "retry", "failed"):
        raise HTTPException(status_code=409, detail=f"job is not resumable (status={row['status']})")
    if row["id"] in state.queue._paused_kinds:
        raise HTTPException(status_code=409, detail="job is paused, use /api/v1/queue/pause first")

    # resume the upload session from its stored offset (if it still exists)
    payload = _payload_dict(row)
    payload.pop("upload_metrics", None)
    session_id = payload.get("session_id")
    resumed_offset = 0
    session = None
    if session_id:
        session = await UploadSessionRepo(db).get(session_id)
        if session:
            resumed_offset = int(session.get("offset") or 0)
    # Never keep a dangling session pointer: it would make the next attempt
    # claim a bogus resume offset.
    if session:
        payload["session_id"] = session_id
    else:
        payload.pop("session_id", None)
    payload["resumed_offset"] = resumed_offset

    await state.queue.jobs.update_fields(
        job_id,
        status="pending",
        attempts=0,
        next_run_at=0,
        error="",
        finished_at=None,
        lease_owner="",
        lease_until=0,
        payload=payload,
    )

    state.queue._wake.set()
    return {
        "job_id": job_id,
        "status": "pending",
        "resumed_offset": resumed_offset,
        "session_id": session_id,
        "msg": "آپلود از offset ریستور شد" if resumed_offset else "آپلود شروع به‌صورت تازه شد",
    }


@router.get("/transfer-progress")
async def transfer_progress(_: str = Depends(get_current_admin), db=Depends(get_db)):
    """Live snapshot of in-flight transfer jobs (files tab progress badge).

    Jobs that finished within the last few seconds are still reported (pct=100)
    so tiny transfers — done in milliseconds on a fast backend — remain
    observable instead of blinking in and out between polls.
    """
    from ..core.models import now

    rows = await db.fetch_all(
        "SELECT id, status, payload, attempts FROM jobs WHERE kind='transfer'"
        " AND (status IN ('running','pending','retry') OR (status='done' AND finished_at >= ?))"
        " ORDER BY seq DESC LIMIT 200",
        (now() - 3.0,),
    )
    items = []
    for r in rows:
        try:
            import json as _json

            p = _json.loads(r["payload"] or "{}")
        except Exception:
            continue
        prog = p.get("progress") or {}
        file_id = p.get("file_id") or prog.get("file_id") or ""
        bytes_total = int(prog.get("bytes_total") or 0)
        parts_total = int(prog.get("parts_total") or 0)
        file_name = prog.get("file_name") or ""
        if not bytes_total and file_id:
            # job not started yet → take totals from the file record
            from ..core.models import FileRepo

            rec = await FileRepo(db).get(file_id)
            if rec:
                file_name = file_name or rec["name"]
                pl = await FileRepo(db).parts(file_id)
                bytes_total = sum(int(x.get("size") or 0) for x in pl)
                parts_total = parts_total or len(pl)
        items.append(
            {
                "job_id": r["id"],
                "status": "running",
                "file_id": file_id,
                "file_name": file_name,
                "pct": int(p.get("progress_pct") or 0) if r["status"] == "running" else (100 if r["status"] == "done" else 0),
                "bytes_done": int(prog.get("bytes_done") or 0),
                "bytes_total": bytes_total,
                "parts_total": parts_total,
                "target_chat": prog.get("target_chat") or p.get("target_chat") or "",
                "attempts": int(r["attempts"] or 0),
            }
        )
    return {"items": items, "running": sum(1 for i in items if i["pct"] > 0 or i["job_id"])}


class PauseIn(BaseModel):
    kind: str


@router.post("/pause")
async def pause(body: PauseIn, _: str = Depends(get_current_admin)):
    if body.kind not in ("download", "upload", "delete"):
        raise HTTPException(status_code=400, detail="unknown kind")
    state.queue.pause(body.kind)
    return {"paused": state.queue.paused()}


@router.post("/resume")
async def resume(body: PauseIn, _: str = Depends(get_current_admin)):
    state.queue.resume(body.kind)
    return {"paused": state.queue.paused()}


@router.post("/retry/{job_id}")
async def retry_job(job_id: str, _: str = Depends(get_current_admin), db=Depends(get_db)):
    row = await state.queue.jobs.fetch(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("failed",):
        raise HTTPException(status_code=409, detail="job is not failed")

    # Reset the same row instead of enqueueing a duplicate.  For uploads, keep
    # the stored session offset so a partial transfer resumes instead of
    # restarting from zero.
    payload = _payload_dict(row)
    payload.pop("upload_metrics", None)
    resumed_offset = 0
    session_id = payload.get("session_id")
    if row["kind"] == "upload" and session_id:
        from ..core.models import UploadSessionRepo

        session = await UploadSessionRepo(db).get(session_id)
        if session:
            resumed_offset = int(session.get("offset") or 0)
            payload["resumed_offset"] = resumed_offset
        else:
            payload.pop("session_id", None)
            payload.pop("resumed_offset", None)

    await state.queue.jobs.update_fields(
        job_id,
        status="pending",
        attempts=0,
        next_run_at=0,
        error="",
        finished_at=None,
        lease_owner="",
        lease_until=0,
        payload=payload,
    )
    state.queue._wake.set()
    return {
        "ok": True,
        "job_id": job_id,
        "resumed_offset": resumed_offset,
        "session_id": session_id if resumed_offset or payload.get("session_id") else None,
    }


@router.post("/purge")
async def purge(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import now

    n = await state.queue.jobs.purge_finished(now() - 0)
    return {"purged": n}
