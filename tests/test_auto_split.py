"""Optional automatic multi-account split: planning, balanced parts, real upload.

The main ``split_threshold`` path stays sequential on one account.  This suite
covers the separate auto-split path that distributes balanced parts across free
upload accounts when the runtime setting is enabled.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os

import pytest

from app.core.models import AccountRepo
from app.core.security import encrypt_str
from app.core.settings_service import runtime_settings
from app.core.state import get_db, state
from app.queue.queue_manager import QueueManager


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


async def _wait_ready(client, api_key, fid, tries=300):
    rec = {}
    for _ in range(tries):
        rec = (await client.get(f"/api/v1/files/{fid}", headers=_H(api_key))).json()
        if rec.get("status") in ("ready", "failed", "error"):
            return rec
        await asyncio.sleep(0.05)
    return rec


@pytest.mark.asyncio
async def test_split_file_balanced_keeps_every_byte(tmp_path):
    src = tmp_path / "balanced.bin"
    payload = os.urandom(1001)
    src.write_bytes(payload)

    paths = await QueueManager._split_file_balanced(str(src), 3)
    try:
        assert [os.path.getsize(p) for p in paths] == [334, 334, 333]
        assert b"".join(open(p, "rb").read() for p in paths) == payload
    finally:
        for path in paths:
            os.remove(path)


@pytest.mark.asyncio
async def test_auto_split_uses_multiple_accounts(client, api_key, token):
    db = await get_db()
    # The fixture supplies one ready fake account; add a second one.
    acc2 = await AccountRepo(db).create("auto-acc-2", "+989120000002", "")
    await AccountRepo(db).set_session(acc2, encrypt_str("fake-session::auto2"), "ready")
    await state.manager.refresh_one_account(acc2)

    admin = {"Authorization": f"Bearer {token}"}
    keys = (
        "auto_split_enabled",
        "auto_split_min_file_size",
        "auto_split_min_part_size",
        "auto_split_max_part_size",
        "auto_split_min_accounts",
        "auto_split_max_accounts",
        "auto_split_max_parts",
        "split_threshold",
    )
    try:
        r = await client.put(
            "/api/v1/admin/settings",
            headers=admin,
            json={
                "auto_split_enabled": 1,
                "auto_split_min_file_size": 0,
                "auto_split_min_part_size": 1024 * 1024,
                "auto_split_max_part_size": 1024 * 1024,
                "auto_split_min_accounts": 2,
                "auto_split_max_accounts": 2,
                "auto_split_max_parts": 8,
                "split_threshold": 1024 * 1024,
            },
        )
        assert r.status_code == 200, r.text

        payload = os.urandom(3 * 1024 * 1024)
        H = _H(api_key)
        r = await client.post(
            "/api/v1/files/upload/session",
            headers={**H, "X-Storage-Chat": "@auto-split-test"},
            json={"name": "auto-split.bin", "size": len(payload), "mime": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        r = await client.patch(
            f"/api/v1/files/upload/session/{sid}",
            headers={**H, "X-Offset": "0"},
            content=payload,
        )
        assert r.status_code == 200 and r.json().get("completed"), r.text
        fid = r.json()["file_id"]

        rec = await _wait_ready(client, api_key, fid)
        assert rec["status"] == "ready", rec
        assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
        parts = rec.get("parts") or []
        assert len(parts) == 3, f"expected three balanced parts, got {parts}"
        assert sum(int(p["size"]) for p in parts) == len(payload)

        # The queue job records every account that carried a part.
        row = await db.fetch_one(
            "SELECT payload FROM jobs WHERE kind='upload' AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
            (f"%{fid}%",),
        )
        handled = json.loads(row["payload"]).get("handled_by", "")
        used = {p.strip() for p in handled.split(",") if p.strip()}
        assert len(used) >= 2, f"auto-split did not distribute across accounts: {handled!r}"

        # The concatenated Telegram parts still download as the original bytes.
        r = await client.get(f"/api/v1/files/{fid}/content", headers=H)
        assert r.status_code == 200 and r.content == payload
    finally:
        for key in keys:
            await db.execute("DELETE FROM settings WHERE key=?", (key,))
        runtime_settings().invalidate()


@pytest.mark.asyncio
async def test_single_upload_compresses_range_and_removes_tmp(client, api_key, token):
    """Compression also covers the ordinary one-part path, and the source tmp
    file is gone once the file is ready."""
    db = await get_db()
    admin = {"Authorization": f"Bearer {token}"}
    try:
        r = await client.put("/api/v1/admin/settings", headers=admin, json={"compress_uploads": 1})
        assert r.status_code == 200, r.text

        payload = b"single-compress-me\n" * 100_000  # ~2 MB, highly compressible
        r = await client.post(
            "/api/v1/files/upload",
            headers=_H(api_key),
            files={"file": ("single-compress.bin", io.BytesIO(payload), "application/octet-stream")},
        )
        assert r.status_code == 200, r.text
        fid = r.json()["file_id"]
        rec = await _wait_ready(client, api_key, fid)
        assert rec["status"] == "ready", rec
        parts = rec.get("parts") or []
        assert len(parts) == 1
        assert parts[0]["encoding"] == "gzip"
        assert 0 < int(parts[0]["stored_size"]) < int(parts[0]["size"])

        H = _H(api_key)
        r = await client.get(f"/api/v1/files/{fid}/content", headers=H)
        assert r.status_code == 200 and r.content == payload
        r = await client.get(
            f"/api/v1/files/{fid}/content",
            headers={**H, "Range": "bytes=1000-1999"},
        )
        assert r.status_code == 206 and r.content == payload[1000:2000]

        row = await db.fetch_one(
            "SELECT payload FROM jobs WHERE kind='upload' AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
            (f"%{fid}%",),
        )
        tmp_path = json.loads(row["payload"])["tmp_path"]
        for _ in range(50):
            if not os.path.exists(tmp_path):
                break
            await asyncio.sleep(0.02)
        assert not os.path.exists(tmp_path), "upload tmp file must be removed after completion"
        assert not os.path.exists(tmp_path + ".gz"), "compressed staging file must be removed"
    finally:
        await db.execute("DELETE FROM settings WHERE key='compress_uploads'")
        runtime_settings().invalidate()


@pytest.mark.asyncio
async def test_auto_split_honors_key_channel_and_compresses(client, token):
    """A key's pinned channel must win over account defaults, even when the
    file is split and gzip-compressed across several accounts."""
    db = await get_db()
    acc2 = await AccountRepo(db).create("auto-acc-3", "+989120000003", "")
    await AccountRepo(db).set_session(acc2, encrypt_str("fake-session::auto3"), "ready")
    await state.manager.refresh_one_account(acc2)

    admin = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/api/v1/keys",
        json={"name": "auto-key-channel", "scopes": "read,write", "storage_chat": "@key-target"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    raw_key = r.json()["key"]

    keys = (
        "auto_split_enabled", "auto_split_min_file_size", "auto_split_min_part_size",
        "auto_split_max_part_size", "auto_split_min_accounts", "auto_split_max_accounts",
        "auto_split_max_parts", "split_threshold", "compress_uploads",
    )
    try:
        r = await client.put(
            "/api/v1/admin/settings",
            headers=admin,
            json={
                "auto_split_enabled": 1,
                "auto_split_min_file_size": 0,
                "auto_split_min_part_size": 1024 * 1024,
                "auto_split_max_part_size": 1024 * 1024,
                "auto_split_min_accounts": 2,
                "auto_split_max_accounts": 2,
                "auto_split_max_parts": 8,
                "split_threshold": 1024 * 1024,
                "compress_uploads": 1,
            },
        )
        assert r.status_code == 200, r.text

        payload = b"\0" * (3 * 1024 * 1024)  # highly compressible
        H = _H(raw_key)
        r = await client.post(
            "/api/v1/files/upload/session",
            headers=H,  # no X-Storage-Chat: the key's channel must be used
            json={"name": "key-target.bin", "size": len(payload), "mime": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        r = await client.patch(
            f"/api/v1/files/upload/session/{sid}",
            headers={**H, "X-Offset": "0"},
            content=payload,
        )
        assert r.status_code == 200 and r.json().get("completed"), r.text
        fid = r.json()["file_id"]

        rec = await _wait_ready(client, raw_key, fid)
        assert rec["status"] == "ready", rec
        assert rec["storage_chat"] == "@key-target"
        parts = rec.get("parts") or []
        assert len(parts) == 3
        assert all(p.get("encoding") == "gzip" for p in parts), parts
        assert all(0 < int(p["stored_size"]) < int(p["size"]) for p in parts), parts

        row = await db.fetch_one(
            "SELECT payload FROM jobs WHERE kind='upload' AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
            (f"%{fid}%",),
        )
        assert json.loads(row["payload"])["storage_chat"] == "@key-target"

        r = await client.get(f"/api/v1/files/{fid}/content", headers=H)
        assert r.status_code == 200 and r.content == payload

        # Range crossing a compressed part boundary must decompress correctly.
        start, end = 1024 * 1024 - 6, 1024 * 1024 + 14
        r = await client.get(
            f"/api/v1/files/{fid}/content",
            headers={**H, "Range": f"bytes={start}-{end}"},
        )
        assert r.status_code == 206, r.text
        assert r.content == payload[start:end + 1]
    finally:
        for key in keys:
            await db.execute("DELETE FROM settings WHERE key=?", (key,))
        runtime_settings().invalidate()
