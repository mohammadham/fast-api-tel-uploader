"""Access-aware account selection: cache, probes, and no-access failures."""
from __future__ import annotations

import asyncio

import pytest

from app.core.models import AccountRepo
from app.core.security import encrypt_str
from app.core.state import get_db, state
from app.services.access import AccessService
from app.tg.fake import FakeBackend
from app.tg.manager import NoBackendAvailable


async def _two_accounts(db):
    rows = await AccountRepo(db).list()
    first = f"acc:{rows[0]['id']}"
    second_id = await AccountRepo(db).create("access-acc-2", "+989120000099", "")
    await AccountRepo(db).set_session(second_id, encrypt_str("fake-session::access2"), "ready")
    await state.manager.refresh_one_account(second_id)
    return first, f"acc:{second_id}"


@pytest.mark.asyncio
async def test_pick_skips_denied_account_and_caches_positive(client):
    db = await get_db()
    denied, allowed = await _two_accounts(db)
    FakeBackend.ACCESS[denied] = {}
    FakeBackend.ACCESS[allowed] = {"@private": {"read", "write"}}

    service = AccessService(db, state.manager)
    borrowed = await service.pick("@private", need="read", preferred=denied)
    try:
        assert borrowed.key == allowed
    finally:
        await borrowed.__aexit__(None, None, None)

    row = await service.get(allowed, "@private")
    assert row is not None and int(row["can_read"]) == 1
    denied_row = await service.get(denied, "@private")
    assert denied_row is not None and int(denied_row["can_read"]) == 0


@pytest.mark.asyncio
async def test_pick_raises_when_every_account_is_denied(client):
    db = await get_db()
    first, second = await _two_accounts(db)
    FakeBackend.ACCESS[first] = {}
    FakeBackend.ACCESS[second] = {}

    service = AccessService(db, state.manager)
    with pytest.raises(NoBackendAvailable):
        await service.pick("@private", need="read")


@pytest.mark.asyncio
async def test_pick_keeps_legacy_fallback_when_backend_cannot_probe(client):
    db = await get_db()
    service = AccessService(db, state.manager)
    # no FakeBackend.ACCESS entry → probe_access returns None → normal pool
    borrowed = await service.pick("@anything", need="read")
    await borrowed.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_upload_and_download_use_authorized_account(client, api_key):
    db = await get_db()
    denied, allowed = await _two_accounts(db)
    FakeBackend.ACCESS[denied] = {}
    FakeBackend.ACCESS[allowed] = {"@private": {"read", "write"}}

    H = {"Authorization": "Bearer " + api_key}
    payload = b"private-channel-payload"
    r = await client.post(
        "/api/v1/files/upload/session",
        headers={**H, "X-Storage-Chat": "@private"},
        json={"name": "private.bin", "size": len(payload), "mime": "application/octet-stream"},
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

    rec = {}
    for _ in range(100):
        rec = (await client.get(f"/api/v1/files/{fid}", headers=H)).json()
        if rec.get("status") in ("ready", "failed"):
            break
        await asyncio.sleep(0.05)
    assert rec.get("status") == "ready", rec
    assert rec.get("storage_chat") == "@private"

    r = await client.get(f"/api/v1/files/{fid}/content", headers=H)
    assert r.status_code == 200, r.text
    assert r.content == payload


@pytest.mark.asyncio
async def test_key_preflight_and_access_health_report(client, token):
    db = await get_db()
    denied, allowed = await _two_accounts(db)
    FakeBackend.ACCESS[denied] = {}
    FakeBackend.ACCESS[allowed] = {"@preflight": {"write", "read"}}

    admin = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/api/v1/keys/preflight",
        headers=admin,
        json={"backend": "telegram", "storage_chat": "@preflight"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["writable"] >= 1
    assert {a["key"] for a in body["accounts"]} >= {denied, allowed}

    r = await client.get("/api/v1/admin/access-health", headers=admin)
    assert r.status_code == 200, r.text
    health = r.json()
    assert health["chat_access"]["total"] >= 2
    assert health["chat_access"]["active"] >= 2
    assert health["orphan_parts"]["total"] >= 0
