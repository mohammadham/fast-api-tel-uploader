"""Persistence test: pending jobs survive queue restart (recovery from SQLite)."""
from __future__ import annotations

import asyncio

from app.core.state import get_db, state
from app.queue.queue_manager import QueueManager


async def test_jobs_recovered_after_restart(client, api_key):
    db = await get_db()
    queue = state.queue

    # enqueue a paused-kind upload job so it stays pending
    queue.pause("upload")
    fid = "f_persist1"
    from app.core.models import FileRepo

    await FileRepo(db).create(fid, "p.bin", 4, "application/octet-stream")
    job_id = await queue.enqueue("upload", {"file_id": fid, "tmp_path": "/nonexistent"}, 40)
    await asyncio.sleep(0.1)

    # simulate restart: stop old queue, start a new one on same DB
    await queue.stop()
    queue2 = QueueManager(db, state.manager)
    await queue2._recover()

    rows = await db.fetch_all("SELECT id, status FROM jobs WHERE id=?", (job_id,))
    assert rows, "job row missing"
    assert rows[0]["status"] in ("pending", "retry", "running")

    # the recovered heap must contain the job
    heap_ids = [jid for _, _, jid in queue2._heap]
    assert job_id in heap_ids

    queue2._stopped = True  # don't actually run workers in test
    queue.resume("upload")


async def test_running_jobs_reset_to_pending(client, api_key):
    db = await get_db()
    await db.execute(
        "INSERT INTO jobs(id, kind, priority, seq, status, payload, created_at, updated_at)"
        " VALUES('j_x1','upload',40,1,'running','{}',0,0)"
    )
    queue = QueueManager(db, state.manager)
    await queue._recover()
    row = await db.fetch_one("SELECT status FROM jobs WHERE id='j_x1'")
    assert row["status"] == "pending"
