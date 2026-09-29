"""Bulk multi-select transfer: /files/bulk-transfer enqueues transfer jobs that
re-home a ready file's parts into a new storage chat.

Covered here: validation, dedupe skips, folder re-home, and the job actually
running through the queue in fake-TG (STORE entries move to the target chat).
"""
from __future__ import annotations

import asyncio
import json
import time

from app.core.state import get_db, state


H = None  # set per-test


async def _make_ready_file(db, name: str, size: int = 32) -> str:
    from app.core.models import FileRepo, new_id

    fid = new_id("f")
    await FileRepo(db).create(fid, name, size, "application/octet-stream")
    await db.execute(
        "UPDATE files SET status='ready', storage_chat='me' WHERE id=?", (fid,)
    )
    return fid


async def _wait_queue_idle(timeout: float = 10.0) -> None:
    db = await get_db()
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE kind='transfer' AND status NOT IN ('done','failed')"
        )
        if row and int(row["n"]) == 0:
            done = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM jobs WHERE kind='transfer' AND status='done'"
            )
            if done and int(done["n"]) > 0:
                return
        await asyncio.sleep(0.1)
    raise AssertionError("transfer jobs did not finish in time")


async def test_bulk_transfer_validation(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/v1/files/bulk-transfer", json={"file_ids": [], "storage_chat": "@x"}, headers=hdr)
    assert r.status_code == 400
    r = await client.post("/api/v1/files/bulk-transfer", json={"file_ids": ["f_1"], "storage_chat": "not-a-chat"}, headers=hdr)
    assert r.status_code == 400


async def test_bulk_transfer_skips_and_enqueues(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()
    fid_ok = await _make_ready_file(db, "move-me.bin")
    # a file already sitting in the target chat must be skipped, not re-sent
    fid_same = await _make_ready_file(db, "already-there.bin")
    await db.execute("UPDATE files SET storage_chat='@target-chan', parts=1 WHERE id=?", (fid_same,))
    r = await client.post(
        "/api/v1/files/bulk-transfer",
        json={"file_ids": [fid_ok, "f_missing", fid_same, fid_ok], "storage_chat": "@target-chan"},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enqueued"] == [fid_ok]
    reasons = [f["reason"] for f in data["failed"]]
    assert any("not found" in x for x in reasons)
    assert any("already in target" in x for x in reasons)
    assert any("duplicate" in x for x in reasons)


async def test_transfer_job_moves_fake_store_parts(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()
    from app.tg.fake import FakeBackend

    FakeBackend.STORE.clear()
    # seed a ready file whose part lives in fake STORE under chat 'me'
    fid = await _make_ready_file(db, "stored.bin", 11)
    await db.execute(
        "INSERT INTO file_parts(file_id, idx, message_id, size) VALUES(?,?,?,?)",
        (fid, 0, 4242, 11),
    )
    FakeBackend.STORE[("me", 4242)] = (b"transfer-me", "application/octet-stream")

    r = await client.post(
        "/api/v1/files/bulk-transfer",
        json={"file_ids": [fid], "storage_chat": "@dest"},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["enqueued"]) == 1
    await _wait_queue_idle()

    rec = await db.fetch_one("SELECT * FROM files WHERE id=?", (fid,))
    assert rec["storage_chat"] == "@dest"
    new_ids = json.loads(rec["message_ids"])
    assert new_ids and new_ids != [4242]
    # new chat holds the same bytes; old chat copy left untouched (safety net)
    assert FakeBackend.STORE[("@dest", new_ids[0])][0] == b"transfer-me"
    assert FakeBackend.STORE[("me", 4242)][0] == b"transfer-me"


async def test_transfer_with_folder_rehome(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()
    from app.core.models import FolderRepo

    folder_id = await FolderRepo(db).create("bulk-move-target", None)
    fid = await _make_ready_file(db, "with-folder.bin")
    r = await client.post(
        "/api/v1/files/bulk-transfer",
        json={"file_ids": [fid], "storage_chat": "@dest2", "folder_id": folder_id},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    rec = await db.fetch_one("SELECT folder_id FROM files WHERE id=?", (fid,))
    assert rec["folder_id"] == folder_id
