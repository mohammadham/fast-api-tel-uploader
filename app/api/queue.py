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
