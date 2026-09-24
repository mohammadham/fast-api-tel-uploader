"""Runtime settings + starter flag + nodes tests (SQLite mode)."""
from __future__ import annotations

import pytest

from app.core.state import get_db


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def test_settings_list_requires_auth(client):
    resp = await client.get("/api/v1/admin/settings")
    assert resp.status_code == 401


async def test_settings_list_shape(client, admin_headers):
    resp = await client.get("/api/v1/admin/settings", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    keys = {i["key"] for i in items}
    assert {
        "max_upload_size",
        "default_key_rpm",
        "download_workers",
        "upload_workers",
        "presigned_ttl",
        "default_backend",
    } <= keys
    for i in items:
        assert i["source"] in ("db", "env")
        assert "current" in i


async def test_settings_save_and_validate(client, admin_headers):
    # valid update
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"max_upload_size": 12345, "default_backend": "eitaa"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert set(resp.json()["applied"]) == {"max_upload_size", "default_backend"}
    # DB value now wins
    lst = (await client.get("/api/v1/admin/settings", headers=admin_headers)).json()["items"]
    by_key = {i["key"]: i for i in lst}
    assert by_key["max_upload_size"]["current"] == 12345
    assert by_key["max_upload_size"]["source"] == "db"
    assert by_key["default_backend"]["current"] == "eitaa"


async def test_settings_rejects_unknown_and_bad_values(client, admin_headers):
    resp = await client.put("/api/v1/admin/settings", json={"not_a_key": 1}, headers=admin_headers)
    assert resp.status_code == 400
    resp = await client.put("/api/v1/admin/settings", json={"max_upload_size": -5}, headers=admin_headers)
    assert resp.status_code == 400
    resp = await client.put("/api/v1/admin/settings", json={"default_backend": "ftp"}, headers=admin_headers)
    assert resp.status_code == 400


async def test_settings_reset(client, admin_headers):
    await client.put("/api/v1/admin/settings", json={"presigned_ttl": 99}, headers=admin_headers)
    resp = await client.post("/api/v1/admin/settings/reset", headers=admin_headers)
    assert resp.status_code == 200
    lst = (await client.get("/api/v1/admin/settings", headers=admin_headers)).json()["items"]
    by_key = {i["key"]: i for i in lst}
    assert by_key["presigned_ttl"]["source"] == "env"


async def test_runtime_max_upload_size_enforced(client, admin_headers, api_key):
    # shrink limit via runtime settings, then verify upload rejects a bigger file
    resp = await client.put("/api/v1/admin/settings", json={"max_upload_size": 10}, headers=admin_headers)
    assert resp.status_code == 200
    resp = await client.post(
        "/api/v1/files/upload/session",
        json={"name": "big.bin", "size": 1000},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 413


async def test_setup_status_and_complete(client, admin_headers):
    st = (await client.get("/api/v1/admin/setup/status", headers=admin_headers)).json()
    assert st["initialized"] is True  # env password present → auto-initialized


async def test_setup_flow_fresh_install(client, admin_headers):
    # simulate a fresh install: clear the flag, then complete the wizard
    db = await get_db()
    from app.core.settings_service import get_meta, set_meta

    await set_meta(db, "initialized", "0")
    st = (await client.get("/api/v1/admin/setup/status", headers=admin_headers)).json()
    assert st["initialized"] is False
    resp = await client.post(
        "/api/v1/admin/setup/complete",
        json={"new_password": "", "default_backend": "eitaa"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert (await client.get("/api/v1/admin/setup/status", headers=admin_headers)).json()["initialized"] is True
    assert (await get_meta(db, "initialized")) == "1"
    # reset backend so other tests are unaffected
    await client.put("/api/v1/admin/settings", json={"default_backend": "telegram"}, headers=admin_headers)


async def test_nodes_registered(client, admin_headers):
    resp = await client.get("/api/v1/admin/nodes", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "node_id" in data
    assert isinstance(data["items"], list)
