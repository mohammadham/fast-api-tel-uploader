"""Per-bot test endpoint + 24h handled counter (panel bots tab) + upload gate.

Regression context: the bots tab had no per-bot test action at all, and nothing
recorded which backend handled a queue job, so per-account/per-bot load could
not be observed. The upload-pressure setting (max_concurrent_uploads) existed
in the settings UI but was never wired to the queue.
"""
from __future__ import annotations

import time

from app.core.models import Job, JobRepo, KIND_UPLOAD, new_id
from app.core.state import get_db, state


H = None  # filled per-test via token fixture


async def test_bot_test_endpoint_fake_tg(client, token):
    """POST /bots/{id}/test probes the bot's own backend via a tiny send."""
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/v1/bots", json={"token": "123456:AAHfakeTokenForUiReview1234567890", "label": "t-bot"}, headers=hdr)
    assert r.status_code == 200, r.text
    bot_id = r.json()["id"]

    r = await client.post(f"/api/v1/bots/{bot_id}/test", headers=hdr)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data
    assert data["message_id"]
    assert data["bot_id"] == bot_id

    # status row flips to ready and the probe is audited
    items = (await client.get("/api/v1/bots", headers=hdr)).json()["items"]
    row = next(b for b in items if b["id"] == bot_id)
    assert row["status"] == "ready"


async def test_bot_list_has_handled_24h(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/v1/bots", json={"token": "123456:AAHfakeTokenForUiReview1234567891", "label": "t-bot2"}, headers=hdr)
    bot_id = r.json()["id"]
    items = (await client.get("/api/v1/bots", headers=hdr)).json()["items"]
    row = next(b for b in items if b["id"] == bot_id)
    assert "handled_24h" in row and row["handled_24h"] == 0


async def test_handled_24h_counts_jobs_per_backend(client, token):
    """A done upload job records handled_by in its payload; counters read it."""
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()
    job_id = new_id("j")
    job = Job(
        id=job_id, kind=KIND_UPLOAD, priority=40, seq=1,
        payload={"file_id": "f_x", "handled_by": "acc:1"},
        status="done", created_at=time.time(), updated_at=time.time(),
        finished_at=time.time(),
    )
    await JobRepo(db).insert(job)
    await db.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?", (time.time(), job_id)
    )

    accounts = (await client.get("/api/v1/accounts", headers=hdr)).json()["items"]
    acc1 = next(a for a in accounts if a["id"] == 1)
    assert acc1["handled_24h"] >= 1


async def test_upload_gate_caps_concurrency(client, token):
    """max_concurrent_uploads runtime setting drives a global semaphore."""
    from app.core.config import get_settings

    db = await get_db()
    q = state.queue
    s = get_settings()

    # default cap from env settings
    sem = await q._upload_semaphore()
    assert sem._value >= 1

    # runtime override changes the gate capacity
    await db.execute(
        "INSERT INTO settings(key, value, updated_at, updated_by) VALUES('max_concurrent_uploads','1',?, 't')"
        " ON CONFLICT(key) DO UPDATE SET value='1'",
        (time.time(),),
    )
    from app.core.settings_service import runtime_settings

    runtime_settings().invalidate()
    sem2 = await q._upload_semaphore()
    assert sem2 is not sem  # rebuilt
    assert sem2._value == 1
    # cleanup: restore default so other tests are unaffected
    await db.execute("DELETE FROM settings WHERE key='max_concurrent_uploads'")
    runtime_settings().invalidate()
