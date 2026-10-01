"""Queue inspection & control: stats, jobs list, pause/resume kinds, purge."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


@router.get("/stats")
async def stats(_: str = Depends(get_current_admin), db=Depends(get_db)):
    return await state.queue.stats()


@router.get("/jobs")
async def jobs(limit: int = 100, _: str = Depends(get_current_admin), db=Depends(get_db)):
    return {"items": await state.queue.jobs.recent(min(limit, 500)), "paused": state.queue.paused()}


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
    await state.queue.jobs.update_fields(job_id, status="pending", attempts=0, next_run_at=0, error="")
    # re-enqueue a fresh job with same kind/payload (simplest safe path)
    import json

    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    await state.queue.enqueue(row["kind"], payload, int(row["priority"]))
    return {"ok": True}


@router.post("/purge")
async def purge(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import now

    n = await state.queue.jobs.purge_finished(now() - 0)
    return {"purged": n}
