"""Channels API — registry CRUD, probe test, JSON backup/restore round-trip.

The channel feature is a registry + probe + self-describing JSON backup:
- POST /api/v1/channels registers a chat (normalized, deduped)
- POST /api/v1/channels/{id}/test sends a probe file through acc:/bot: backends
- GET  /api/v1/channels/{id}/backup serializes the channel's file rows into a
  JSON manifest and uploads it *to the channel itself* (#tgdrive-backup);
  the manifest message_id is persisted on the channel row
- POST /api/v1/channels/{id}/restore downloads that manifest back and merges
  rows (existing local file ids always win)
- GET  /api/v1/channels/{id}/content returns the stored manifest JSON

Runs entirely against the in-memory FakeTelegram (TGDRIVE_FAKE_TG=1), so the
round-trip proves manifest bytes survive send_document → iter_file intact.
"""
from __future__ import annotations

import asyncio
import io

from app.core.models import FileRepo
from app.core.state import state
from app.tg.fake import FakeBackend


async def _admin_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_channel(client, token: str, chat: str, label: str = "تست") -> int:
    r = await client.post(
        "/api/v1/channels", json={"chat": chat, "label": label}, headers=await _admin_headers(token)
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _upload_into_chat(client, token: str, chat: str, name="doc.txt", payload=b"hello backup") -> str:
    """Admin API key pinned to `chat`, upload one file, wait until ready."""
    r = await client.post(
        "/api/v1/keys",
        json={"name": "ch-test-key", "scopes": "read,write", "rpm": 600, "storage_chat": chat},
        headers=await _admin_headers(token),
    )
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    H = {"Authorization": f"Bearer {key}"}
    r = await client.post(
        "/api/v1/files/upload", headers=H, files={"file": (name, io.BytesIO(payload), "text/plain")}
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    status = "queued"
    for _ in range(200):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        status = r.json().get("status", "")
        if status == "ready":
            break
        await asyncio.sleep(0.05)
    assert status == "ready", f"file never became ready: {status}"
    return fid


async def test_channel_registry_crud(client, token):
    AH = await _admin_headers(token)
    cid = await _make_channel(client, token, chat="@reg-test")

    # duplicate chat → 409
    r = await client.post("/api/v1/channels", json={"chat": "@reg-test"}, headers=AH)
    assert r.status_code == 409

    # normalization: bare numeric id gets -100 prefix; bad chat → 400
    r = await client.post("/api/v1/channels", json={"chat": "123456789"}, headers=AH)
    assert r.status_code == 200 and r.json()["chat"] == "-100123456789"
    r = await client.post("/api/v1/channels", json={"chat": "bad chat!"}, headers=AH)
    assert r.status_code == 400

    # list carries the entry
    r = await client.get("/api/v1/channels", headers=AH)
    items = r.json()["items"]
    row = next(c for c in items if c["id"] == cid)
    assert row["chat"] == "@reg-test" and row["enabled"] == 1 and row["status"] == "unknown"

    # rename + toggle
    r = await client.patch(f"/api/v1/channels/{cid}", json={"label": "برچسب نو"}, headers=AH)
    assert r.status_code == 200
    r = await client.post(f"/api/v1/channels/{cid}/toggle", headers=AH)
    assert r.json()["enabled"] is False

    # delete → gone
    r = await client.delete(f"/api/v1/channels/{cid}", headers=AH)
    assert r.status_code == 200
    r = await client.get("/api/v1/channels", headers=AH)
    assert not any(c["id"] == cid for c in r.json()["items"])


async def test_channel_probe_sends_real_file(client, token):
    AH = await _admin_headers(token)
    cid = await _make_channel(client, token, chat="@probe-test")
    before = len(FakeBackend.STORE)

    r = await client.post(f"/api/v1/channels/{cid}/test", headers=AH)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["backend"].startswith("acc:")
    assert data["message_id"] in [m for (chat, m) in FakeBackend.STORE if chat == "@probe-test"]
    assert len(FakeBackend.STORE) == before + 1

    # status persisted on the row
    items = (await client.get("/api/v1/channels", headers=AH)).json()["items"]
    row = next(c for c in items if c["id"] == cid)
    assert row["status"] == "ok"


async def test_channel_backup_restore_roundtrip(client, token):
    AH = await _admin_headers(token)
    chat = "@roundtrip"
    cid = await _make_channel(client, token, chat=chat)

    fid = await _upload_into_chat(client, token, chat, name="doc.txt", payload=b"payload-roundtrip-1")

    # backup: manifest built from the file table + uploaded into the channel
    r = await client.get(f"/api/v1/channels/{cid}/backup", headers=AH)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is True and b["files"] == 1 and b["bytes"] == len(b"payload-roundtrip-1")
    mid = b["message_id"]
    assert (chat, mid) in FakeBackend.STORE
    assert FakeBackend.CAPTIONS.get(mid, "").startswith("#tgdrive-backup")

    # channel row records the backup pointer
    row = next(c for c in (await client.get("/api/v1/channels", headers=AH)).json()["items"] if c["id"] == cid)
    assert row["last_backup_message_id"] == mid and row["last_backup_files"] == 1

    # content endpoint returns the stored manifest
    r = await client.get(f"/api/v1/channels/{cid}/content", headers=AH)
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["kind"] == "channel-backup"
    names = [f["name"] for f in manifest["files"]]
    assert "doc.txt" in names
    assert manifest["files"][0]["id"] == fid

    # restore #1: local row still exists → skipped, nothing inserted
    r = await client.post(f"/api/v1/channels/{cid}/restore", headers=AH)
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 0 and r.json()["skipped"] == 1

    # wipe the local row, restore again → the file comes back
    await FileRepo(state.db).delete(fid)
    r = await client.post(f"/api/v1/channels/{cid}/restore", headers=AH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1 and fid in body["file_ids"]
    got = await FileRepo(state.db).get(fid)
    assert got["name"] == "doc.txt" and got["storage_chat"] == chat and got["status"] == "ready"


async def test_restore_without_backup_fails_clean(client, token):
    AH = await _admin_headers(token)
    cid = await _make_channel(client, token, chat="@no-backup-yet")
    r = await client.post(f"/api/v1/channels/{cid}/restore", headers=AH)
    assert r.status_code == 400
    assert "no backup" in r.json()["detail"]
