"""Admin pressure alerts: upload-queue stall detection + per-backend flood bursts."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.models import FileRepo, now
from app.core.state import get_db, state
from app.tg.base import FloodWait


async def _mk_upload_job(queue, fid: str, *, status: str = "pending", next_run_at: float = 0.0):
    """Seed an upload job row straight into the in-memory heap (no workers needed)."""
    db = await get_db()
    await FileRepo(db).create(fid, f"{fid}.bin", 32, "application/octet-stream")
    queue._rows[fid] = {
        "id": fid,
        "kind": "upload",
        "priority": 40,
        "seq": 1,
        "status": status,
        "payload": "{}",
        "attempts": 0,
        "max_retries": 5,
        "next_run_at": next_run_at,
        "created_at": now(),
    }


@pytest.fixture()
def quiet_notify(monkeypatch):
    """Capture admin notifications instead of sending them (no bots configured anyway)."""
    sent = []

    async def fake_notify(text):
        sent.append(text)

    import app.services.notify as notify_mod

    monkeypatch.setattr(notify_mod, "notify_admins", fake_notify)
    yield sent


async def test_stall_alert_fires_after_threshold(client, quiet_notify):
    """Uploads waiting behind a full gate past the threshold → alert exactly once."""
    queue = state.queue
    queue._stall_since = None
    queue._stall_notified = False
    # simulate a full upload gate (runtime max_concurrent_uploads = 1 in tests)
    await queue._upload_semaphore()  # ensure the gate exists
    while queue._upload_gate._value > 0:
        await queue._upload_gate.acquire()
    await _mk_upload_job(queue, "f_stall1")
    await _mk_upload_job(queue, "f_stall2")

    # first pass with threshold 0 → feature off, no state
    from app.core.settings_service import runtime_settings

    cache = runtime_settings()._cache
    cache["upload_stall_threshold_s"] = 0
    await queue.check_upload_stall()
    assert not quiet_notify
    assert queue._stall_since is None

    # enable: threshold 5s → not yet stalled (job age < threshold)
    cache["upload_stall_threshold_s"] = 5
    await queue.check_upload_stall()
    assert not quiet_notify
    assert queue._stall_since is None  # no alert state while under threshold

    # age the JOB past the threshold → exactly one alert
    queue._rows["f_stall1"]["created_at"] = now() - 30
    await queue.check_upload_stall()
    await asyncio.sleep(0)  # let the fire-and-forget notify task run
    assert len(quiet_notify) == 1
    assert "معطل" in quiet_notify[0]
    assert queue._stall_notified is True

    # still stalled → no duplicate alert
    await queue.check_upload_stall()
    assert len(quiet_notify) == 1

    # recovery → clear notice, state reset
    queue._upload_gate._value = 1  # slot freed
    await queue.check_upload_stall()
    await asyncio.sleep(0)
    assert len(quiet_notify) == 2
    assert "رفع معطلی" in quiet_notify[1]
    assert queue._stall_since is None

    # stall again after recovery → re-alerts (new burst)
    while queue._upload_gate._value > 0:
        await queue._upload_gate.acquire()
    await _mk_upload_job(queue, "f_stall3")
    queue._rows["f_stall3"]["created_at"] = now() - 30
    await queue.check_upload_stall()
    await asyncio.sleep(0)
    assert len(quiet_notify) == 3


async def test_stall_snapshot_in_stats(client, quiet_notify):
    """stats() carries stall + flooded info for the panel banner."""
    queue = state.queue
    await queue._upload_semaphore()
    while queue._upload_gate._value > 0:
        await queue._upload_gate.acquire()
    await _mk_upload_job(queue, "f_snap1")
    s = await queue.stats()
    assert s["stall"]["gate_full"] is True
    assert s["stall"]["waiting"] == 1
    assert s["stall"]["threshold"] >= 0
    assert s["flooded"] == []
    queue._upload_gate._value = 1  # don't leak a full gate into other tests


async def test_flood_burst_alert_on_threshold(client, quiet_notify):
    """Repeated FloodWaits on one backend cross the threshold → one alert, then cooldown."""
    from app.core.settings_service import runtime_settings

    runtime_settings()._cache["flood_alert_threshold"] = 3
    runtime_settings()._cache["flood_alert_cooldown_s"] = 600
    mgr = state.manager
    key = "acc:1"
    mgr._flood_alerts.pop(key, None)
    try:
        for _ in range(2):
            mgr._note_flood_burst(key, FloodWait(30))
        assert not quiet_notify  # below threshold
        mgr._note_flood_burst(key, FloodWait(30))  # 3rd → alert
        await asyncio.sleep(0)  # let the bg notify task run
        assert len(quiet_notify) == 1
        assert "acc:1" in quiet_notify[0]
        # burst counter reset; next hits start a fresh burst silently
        mgr._note_flood_burst(key, FloodWait(30))
        mgr._note_flood_burst(key, FloodWait(30))
        assert len(quiet_notify) == 1
        # healthy release clears the tracker entirely
        await mgr.release(key, None)
        assert key not in mgr._flood_alerts
    finally:
        mgr._flood_alerts.pop(key, None)


async def test_flood_burst_gap_resets(client, quiet_notify):
    """A long healthy gap between floods restarts the burst count."""
    from app.core.settings_service import runtime_settings

    runtime_settings()._cache["flood_alert_threshold"] = 3
    runtime_settings()._cache["flood_alert_cooldown_s"] = 600
    mgr = state.manager
    key = "acc:1"
    mgr._flood_alerts.pop(key, None)
    try:
        mgr._note_flood_burst(key, FloodWait(30))
        mgr._note_flood_burst(key, FloodWait(30))
        # backdate the last hit so the next one looks like a fresh burst
        mgr._flood_alerts[key]["last_at"] = time.time() - 400
        mgr._note_flood_burst(key, FloodWait(30))
        st = mgr._flood_alerts[key]
        assert st["count"] == 1  # reset happened, only the new hit counts
        assert not quiet_notify
    finally:
        mgr._flood_alerts.pop(key, None)


async def test_pressure_endpoint_shape(token, quiet_notify):
    """GET /queue/pressure returns stall+flooded+summary for the banner."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/v1/queue/pressure", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert set(d) == {"stall", "flooded", "summary"}
        assert {"stalled", "waiting", "threshold", "oldest_waiting_s", "gate_full", "gate_capacity", "upload_workers"} <= set(d["stall"])
        assert isinstance(d["flooded"], list)
        assert isinstance(d["summary"], str)
        # admin-only
        r2 = await ac.get("/api/v1/queue/pressure")
        assert r2.status_code in (401, 403)
