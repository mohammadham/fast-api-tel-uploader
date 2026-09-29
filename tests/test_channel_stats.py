"""Per-channel stats in GET /api/v1/channels.

The channels tab and the files-tab filter select both need file counts and
stored bytes per channel. The aggregate counts ready, non-deleted files per
storage_chat (empty chat → system default). default_stats carries the same
numbers for the system-default chat even when no registry row exists.
"""
from __future__ import annotations

import asyncio
import json
import time

from app.core.models import FileRepo, new_id
from app.core.state import get_db, state


async def _make_channel(client, token: str, chat: str, label: str = "stats") -> int:
    r = await client.post(
        "/api/v1/channels", json={"chat": chat, "label": label}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _seed_ready_file(db, storage_chat: str, size: int) -> str:
    fid = new_id("f")
    await FileRepo(db).create(fid, f"{fid}.bin", size, "application/octet-stream")
    await db.execute(
        "UPDATE files SET status='ready', storage_chat=? WHERE id=?", (storage_chat, fid)
    )
    return fid


async def test_channel_list_stats_and_default_stats(client, token):
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()

    cid = await _make_channel(client, token, "@stats-chan")
    await _seed_ready_file(db, "@stats-chan", 100)
    await _seed_ready_file(db, "@stats-chan", 250)
    # a soft-deleted file must not count
    gone = await _seed_ready_file(db, "@stats-chan", 999)
    await db.execute("UPDATE files SET deleted_at=? WHERE id=?", (time.time(), gone))
    # a not-ready (queued) file must not count either
    queued = await _seed_ready_file(db, "@stats-chan", 500)
    await db.execute("UPDATE files SET status='queued' WHERE id=?", (queued,))

    # the settings default chat is empty in tests (tg_storage_chat="") — seed
    # a file with empty storage_chat so the ''-bucket exists
    await _seed_ready_file(db, "", 42)

    r = await client.get("/api/v1/channels", headers=hdr)
    assert r.status_code == 200, r.text
    d = r.json()
    row = next(c for c in d["items"] if c["chat"] == "@stats-chan")
    assert row["files"] == 2
    assert row["bytes"] == 350

    # default_stats mirrors the ''-bucket (empty storage_chat = system default)
    assert d["default_stats"]["files"] == 1
    assert d["default_stats"]["bytes"] == 42


async def test_files_filter_count_matches_channel_stats(client, token):
    """The number shown in the filter select must match ?storage_chat= total."""
    hdr = {"Authorization": f"Bearer {token}"}
    db = await get_db()
    await _make_channel(client, token, "@stats-chan2")
    for i in range(3):
        await _seed_ready_file(db, "@stats-chan2", 10 + i)

    chan = (await client.get("/api/v1/channels", headers=hdr)).json()
    row = next(c for c in chan["items"] if c["chat"] == "@stats-chan2")
    flt = (await client.get("/api/v1/files", params={"storage_chat": "@stats-chan2"}, headers=hdr)).json()
    assert row["files"] == flt["total"] == 3
