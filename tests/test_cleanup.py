"""Cleanup guarantees for failed or partially retried uploads."""
from __future__ import annotations

import json
import os
import time

import pytest

from app.core.config import get_settings
from app.core.models import FileRepo
from app.core.state import get_db, state
from app.tg.fake import FakeBackend


@pytest.mark.asyncio
async def test_final_failure_removes_parts_tmp_and_records_orphans(client, api_key, token):
    db = await get_db()
    files = FileRepo(db)
    file_id = "cleanup-final-fail"
    await files.create(file_id, "cleanup.bin", 4)
    await db.execute(
        "UPDATE files SET status='uploading', storage_chat='@cleanup-denied' WHERE id=?",
        (file_id,),
    )
    await files.add_part(file_id, 0, 424242, 4, stored_size=4)
    FakeBackend.STORE[("@cleanup-denied", 424242)] = (b"data", "application/octet-stream")

    # Deny every live account so the discard path cannot reach Telegram and
    # has to leave an explicit orphan record instead of silently forgetting it.
    FakeBackend.ACCESS.clear()
    for key in state.manager.account_keys():
        FakeBackend.ACCESS[key] = {"@cleanup-denied": set()}

    tmp_dir = get_settings().final_tmp_dir()
    source = os.path.join(tmp_dir, "cleanup-source.bin")
    derived = os.path.join(tmp_dir, "cleanup-source.bin.part0000")
    for path in (source, derived):
        with open(path, "wb") as fh:
            fh.write(b"cleanup")

    try:
        await state.queue._cleanup_final_upload(
            {"file_id": file_id, "tmp_path": source, "tmp_paths": [derived]},
            "forced failure",
        )
        assert not os.path.exists(source)
        assert not os.path.exists(derived)
        assert int(await db.scalar("SELECT COUNT(*) FROM file_parts WHERE file_id=?", (file_id,)) or 0) == 0
        orphan = await db.fetch_one(
            "SELECT * FROM orphan_parts WHERE file_id=? AND message_id=?", (file_id, 424242)
        )
        assert orphan is not None
        assert orphan["chat_key"] == "@cleanup-denied"
    finally:
        FakeBackend.ACCESS.clear()


@pytest.mark.asyncio
async def test_final_cleanup_keeps_tmp_shared_with_another_active_job(client):
    """A duplicate/overlapping job must not lose its source to the first failure."""
    db = await get_db()
    tmp_dir = get_settings().final_tmp_dir()
    source = os.path.join(tmp_dir, "cleanup-shared.bin")
    with open(source, "wb") as fh:
        fh.write(b"shared cleanup payload")

    now = time.time()
    payload = json.dumps({"file_id": "cleanup-shared", "tmp_path": source})
    for job_id, status in (("cleanup-shared-a", "running"), ("cleanup-shared-b", "retry")):
        await db.execute(
            "INSERT INTO jobs(id, kind, priority, seq, status, payload, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (job_id, "upload", 40, int(now * 1000), status, payload, now, now),
        )

    await state.queue._cleanup_final_upload(
        {"file_id": "cleanup-shared", "tmp_path": source},
        "forced failure",
        job_id="cleanup-shared-a",
    )
    assert os.path.exists(source)

    await db.execute("DELETE FROM jobs WHERE id IN (?,?)", ("cleanup-shared-a", "cleanup-shared-b"))
    await state.queue._cleanup_final_upload(
        {"file_id": "cleanup-shared", "tmp_path": source},
        "forced failure",
        job_id="cleanup-shared-a",
    )
    assert not os.path.exists(source)
