"""Janitor: periodic cleanup of stale tmp files, expired sessions, old keys/jobs."""
import os
import time

import pytest


@pytest.mark.asyncio
async def test_janitor_deletes_stale_tmp_files(client, db):
    from app.core.config import get_settings
    from app.core.models import UploadSessionRepo
    from app.services.janitor import Janitor

    s = get_settings()
    tmp = s.final_tmp_dir()
    os.makedirs(tmp, exist_ok=True)

    now = time.time()
    stale_ts = now - 7200

    # a stale draft file (mtime > 1h old) AND a recent draft file (mtime < 1h)
    stale_path = os.path.join(tmp, f"stale_{int(stale_ts)}.tmp")
    fresh_path = os.path.join(tmp, f"fresh_{int(now)}.tmp")
    open(stale_path, "wb").write(b"stale")
    open(fresh_path, "wb").write(b"fresh")
    os.utime(stale_path, (stale_ts, stale_ts))
    os.utime(fresh_path, (now, now))

    # an expired upload session whose .part tmp file should also be removed
    sess_id = "sess-expired-" + str(int(stale_ts))
    await db.execute(
        "INSERT INTO upload_sessions(id, name, size, mime, offset, folder_path, storage_chat, uploader, created_at) VALUES(?,?,?,?,0,'', '', '', ?)",
        (sess_id, "test", 10, "application/octet-stream", stale_ts),
    )
    part_path = os.path.join(tmp, f"{sess_id}.part")
    open(part_path, "wb").write(b"part")
    os.utime(part_path, (stale_ts, stale_ts))

    await Janitor(db)._sweep()

    assert not os.path.exists(stale_path)
    assert not os.path.exists(part_path)
    assert os.path.exists(fresh_path)  # recent draft kept

    alive = {r["id"] for r in await db.fetch_all("SELECT id FROM upload_sessions")}
    assert sess_id not in alive  # expired session deleted


@pytest.mark.asyncio
async def test_janitor_deletes_expired_sessions(client, db):
    from app.core.config import get_settings
    from app.core.models import UploadSessionRepo
    from app.services.janitor import Janitor

    s = get_settings()
    tmp = s.final_tmp_dir()
    os.makedirs(tmp, exist_ok=True)

    now = time.time()
    old = now - 7200
    for i in range(2):
        sess_id = f"sess-expired-{i}-{int(old)}"
        await db.execute(
            "INSERT INTO upload_sessions(id, name, size, mime, offset, folder_path, storage_chat, uploader, created_at) VALUES(?,?,?,?,0,'', '', '', ?)",
            (sess_id, "test", 10, "application/octet-stream", old),
        )
        part = os.path.join(tmp, f"{sess_id}.part")
        open(part, "wb").write(b"part")
        os.utime(part, (old, old))

    fresh_id = "sess-fresh-" + str(int(now))
    await db.execute(
        "INSERT INTO upload_sessions(id, name, size, mime, offset, folder_path, storage_chat, uploader, created_at) VALUES(?,?,?,?,0,'', '', '', ?)",
        (fresh_id, "test", 10, "application/octet-stream", now),
    )

    await Janitor(db)._sweep()

    alive = {r["id"] for r in await db.fetch_all("SELECT id FROM upload_sessions")}
    assert fresh_id in alive
    for i in range(2):
        assert f"sess-expired-{i}-{int(old)}" not in alive


@pytest.mark.asyncio
async def test_janitor_keeps_recent_tmp_files(client, db):
    from app.core.config import get_settings
    from app.services.janitor import Janitor

    s = get_settings()
    tmp = s.final_tmp_dir()
    os.makedirs(tmp, exist_ok=True)

    now = time.time()
    stale = now - 7200
    fresh = now - 60
    fresh2 = now

    # all three files below the 1h cutoff boundary
    a = os.path.join(tmp, f"a_{int(stale)}.bin")
    b = os.path.join(tmp, f"b_{int(fresh)}.bin")
    c = os.path.join(tmp, f"c_{int(fresh2)}.bin")
    for p in (a, b, c):
        open(p, "wb").write(b"x")
        os.utime(p, (time.time(), time.time()))

    # nudge a and c into the past (older than 1h), b stays very recent
    os.utime(a, (stale, stale))
    os.utime(c, (stale, stale))

    await Janitor(db)._sweep()

    assert not os.path.exists(a)  # older than 1h -> gone
    assert not os.path.exists(c)  # older than 1h -> gone
    assert os.path.exists(b)      # younger than 1h -> kept
