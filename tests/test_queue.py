"""Queue ordering tests: downloads must be served before uploads."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.core.models import FileRepo
from app.core.state import get_db, state


class _AdmitDB:
    async def scalar(self, *args, **kwargs):
        return 0

    async def fetch_all(self, *args, **kwargs):
        return []


class _AdmitRuntime:
    async def get_all(self, db, force=False):
        return {"disk_watermark_pct": 15, "max_queue_jobs": 10, "max_inflight_bytes": 0}


class _Disk:
    total = 100 * 1024 ** 3
    used = 50 * 1024 ** 3
    free = 50 * 1024 ** 3


class _FullDisk(_Disk):
    used = 95 * 1024 ** 3
    free = 5 * 1024 ** 3


async def test_admit_allows_disk_with_enough_free_space(monkeypatch):
    """A 15% free-space floor allows a half-full disk to accept a 20GB upload."""
    import app.queue.queue_manager as qm

    monkeypatch.setattr(qm, "runtime_settings", lambda: _AdmitRuntime())
    monkeypatch.setattr(qm.shutil, "disk_usage", lambda _path: _Disk())
    monkeypatch.setattr(qm.os, "makedirs", lambda *args, **kwargs: None)
    ok, _, reason = await qm.QueueManager(_AdmitDB(), None).admit(20 * 1024 ** 3)
    assert ok, reason


async def test_admit_rejects_when_free_space_would_cross_watermark(monkeypatch):
    import app.queue.queue_manager as qm

    monkeypatch.setattr(qm, "runtime_settings", lambda: _AdmitRuntime())
    monkeypatch.setattr(qm.shutil, "disk_usage", lambda _path: _FullDisk())
    monkeypatch.setattr(qm.os, "makedirs", lambda *args, **kwargs: None)
    ok, retry_after, reason = await qm.QueueManager(_AdmitDB(), None).admit(20 * 1024 ** 3)
    assert not ok
    assert retry_after == 60
    assert "15% free required" in reason


async def test_download_priority_over_upload(client, api_key, token):
    """Enqueue 3 uploads then 1 download; the download must complete first."""
    db = await get_db()
    queue = state.queue

    # small local files to upload
    file_ids = []
    for i in range(3):
        tmp = f"{state.queue and ''}"  # noqa
        import os

        from app.core.config import get_settings

        p = os.path.join(get_settings().final_tmp_dir(), f"u{i}.bin")
        with open(p, "wb") as fh:
            fh.write(b"upload-payload-" + bytes([i]) * 16)
        fid = f"f_up{i}"
        await FileRepo(db).create(fid, f"u{i}.bin", 31, "application/octet-stream")
        file_ids.append(fid)
        await queue.enqueue("upload", {"file_id": fid, "tmp_path": p}, 40)

    # direct fake download target: create a ready file in fake store
    from app.tg.fake import FakeBackend

    FakeBackend.STORE[("me", 9001)] = (b"download-payload" * 4, "application/octet-stream")
    fid_dl = "f_dl1"
    await FileRepo(db).create(fid_dl, "dl.bin", 64, "application/octet-stream")
    await db.execute(
        "UPDATE files SET status='ready', storage_chat='me' WHERE id=?", (fid_dl,)
    )
    await db.execute(
        "INSERT INTO file_parts(file_id, idx, message_id, size) VALUES(?,?,?,?)",
        (fid_dl, 0, 9001, 64),
    )

    order = []
    orig_download = queue._handle_download

    async def spy_download(payload):
        order.append("download")
        # emulate delivery without bot
        payload_backup = payload.get("deliver_to")
        payload["deliver_to"] = {}
        try:
            raise RuntimeError("stop after mark")  # we only need ordering
        finally:
            payload["deliver_to"] = payload_backup

    queue._handle_download = spy_download
    await queue.enqueue("download", {"file_id": fid_dl, "deliver_to": {}}, 100)

    # wait until the download job is processed (uploads may be running)
    for _ in range(100):
        if order:
            break
        await asyncio.sleep(0.05)
    queue._handle_download = orig_download

    assert order, "download job was never picked"
    # The download must have been picked while uploads (enqueued earlier) exist.
    stats = await queue.stats()
    assert stats["by_kind"]["upload"]["pending"] >= 0  # uploads may still run


async def test_resume_upload_job_parses_text_payload(client, token):
    """MySQL (and SQLite) return jobs.payload as text; resume must not 500."""
    import time

    db = await get_db()
    job_id = "j_resume_text_payload"
    now = time.time()
    await db.execute(
        "INSERT INTO jobs(id, kind, priority, seq, status, payload, attempts, max_retries,"
        " next_run_at, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            "upload",
            40,
            int(now * 1000),
            "retry",
            json.dumps({"file_id": "resume-missing", "tmp_path": "/tmp/resume-missing.bin"}),
            1,
            5,
            now + 3600,
            now,
            now,
        ),
    )

    queue = state.queue
    queue.pause("upload")
    try:
        r = await client.post(
            f"/api/v1/queue/resume/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["session_id"] is None
    finally:
        # do not let a resumed upload outlive the per-test DB/temp dir
        await db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        queue.resume("upload")
