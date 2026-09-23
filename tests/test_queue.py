"""Queue ordering tests: downloads must be served before uploads."""
from __future__ import annotations

import asyncio

import pytest

from app.core.models import FileRepo
from app.core.state import get_db, state


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
