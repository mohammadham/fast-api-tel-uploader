"""Admin-triggered manual janitor sweep via POST /api/v1/admin/janitor/run."""
import os
import time

import pytest


@pytest.mark.asyncio
async def test_janitor_admin_run_deletes_stale_tmp_and_expired_sessions(client, db):
    from app.core.config import get_settings
    from app.core.models import Job, JobRepo, UploadSessionRepo

    s = get_settings()
    tmp = s.final_tmp_dir()
    os.makedirs(tmp, exist_ok=True)

    now = time.time()
    stale_ts = now - 7200

    # a stale tmp file (>1h old)
    stale_path = os.path.join(tmp, f"stale_{int(stale_ts)}.bin")
    open(stale_path, "wb").write(b"stale")
    os.utime(stale_path, (stale_ts, stale_ts))

    # an expired upload session whose .part tmp file should also go
    sess_id = "sess-admin-" + str(int(stale_ts))
    await db.execute(
        "INSERT INTO upload_sessions(id, name, size, mime, offset, folder_path, storage_chat, uploader, created_at) VALUES(?,?,?,?,0,'', '', '', ?)",
        (sess_id, "test", 10, "application/octet-stream", stale_ts),
    )
    part_path = os.path.join(tmp, f"{sess_id}.part")
    open(part_path, "wb").write(b"part")
    os.utime(part_path, (stale_ts, stale_ts))

    # run the janitor sweep manually via the admin endpoint (login first)
    login = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    print("DEBUG login status:", login.status_code)
    tok = login.json()["access_token"]
    auth = {"Authorization": "Bearer " + tok}
    print("DEBUG auth header:", auth)
    r = await client.post("/api/v1/admin/janitor/run", headers=auth)
    print("DEBUG admin status:", r.status_code)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["tmp_removed"] == 2, body  # stale .bin + .part
    assert body["sessions_deleted"] == 1, body

    assert not os.path.exists(stale_path), "stale tmp file not removed"
    assert not os.path.exists(part_path), "expired session .part not removed"
    alive = {row["id"] for row in (await db.fetch_all("SELECT id FROM upload_sessions"))}
    assert sess_id not in alive, "expired session not deleted"
    alive_jobs = {row["id"] for row in (await db.fetch_all("SELECT id FROM jobs"))}
    assert "j-admin-" not in alive_jobs, "failed job not purged"


@pytest.mark.asyncio
async def test_janitor_admin_run_rejects_unauthorized(client):
    # no token -> 401
    r = await client.post("/api/v1/admin/janitor/run")
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_janitor_admin_run_rejects_uninitialized_janitor(client):
    # blank out the janitor to simulate an uninitialized app, then call with a
    # valid token: the endpoint should still return 503 (no janitor to run)
    from app.core.state import state

    state.janitor = None
    try:
        login = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200, login.text
        tok = login.json()["access_token"]
        resp = await client.post(
            "/api/v1/admin/janitor/run", headers={"Authorization": f"Bearer {tok}"}
        )
        # authenticated but no janitor -> configured 503
        assert resp.status_code == 503, resp.text
    finally:
        state.janitor = None
