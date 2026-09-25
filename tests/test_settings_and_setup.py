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


async def test_settings_telegram_toggle_and_api_fields(client, admin_headers):
    """fake_tg / eitaa_mode / tg_api_id / tg_api_hash are editable runtime settings."""
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"fake_tg": 1, "eitaa_mode": 1, "tg_api_id": 12345, "tg_api_hash": "a" * 32},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert set(resp.json()["applied"]) == {"fake_tg", "eitaa_mode", "tg_api_id", "tg_api_hash"}
    items = (await client.get("/api/v1/admin/settings", headers=admin_headers)).json()["items"]
    by_key = {i["key"]: i for i in items}
    assert by_key["fake_tg"]["current"] == 1
    assert by_key["fake_tg"]["group"] == "telegram_api"
    assert by_key["eitaa_mode"]["current"] == 1
    assert by_key["tg_api_id"]["current"] == 12345
    assert by_key["tg_api_hash"]["current"] == "a" * 32

    # validation: flags are 0/1 only, api id must be numeric, hash any non-empty str
    for body in ({"fake_tg": 2}, {"eitaa_mode": 7}, {"tg_api_id": -1}):
        r = await client.put("/api/v1/admin/settings", json=body, headers=admin_headers)
        assert r.status_code == 400, body

    # single-key update does not clobber the other saved keys
    r = await client.put("/api/v1/admin/settings", json={"fake_tg": 0}, headers=admin_headers)
    assert r.status_code == 200
    items = (await client.get("/api/v1/admin/settings", headers=admin_headers)).json()["items"]
    by_key = {i["key"]: i for i in items}
    assert by_key["fake_tg"]["current"] == 0
    assert by_key["tg_api_id"]["current"] == 12345
    assert by_key["eitaa_mode"]["current"] == 1


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


async def test_key_storage_chat_create_validate_and_use(client, admin_headers, api_key):
    """Per-key storage_chat: create + validate + upload honors it end-to-end."""
    # create a key with a dedicated storage channel
    resp = await client.post(
        "/api/v1/keys",
        json={"name": "chan-key", "scopes": "read,write", "storage_chat": "-100555"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    raw_key = resp.json()["key"]
    keys = (await client.get("/api/v1/keys", headers=admin_headers)).json()["items"]
    mine = next(k for k in keys if k["key_prefix"] in raw_key)
    assert mine["storage_chat"] == "-100555"

    # invalid values are rejected
    for bad in ("not valid", "@"):
        r = await client.post("/api/v1/keys", json={"name": "x", "storage_chat": bad}, headers=admin_headers)
        assert r.status_code == 400, bad

    # PATCH: set, then clear back to system default
    r = await client.patch(f"/api/v1/keys/{mine['id']}", json={"storage_chat": "@chan"}, headers=admin_headers)
    assert r.status_code == 200
    keys = (await client.get("/api/v1/keys", headers=admin_headers)).json()["items"]
    assert next(k for k in keys if k["id"] == mine["id"])["storage_chat"] == "@chan"
    r = await client.patch(f"/api/v1/keys/{mine['id']}", json={"storage_chat": ""}, headers=admin_headers)
    assert r.status_code == 200
    keys = (await client.get("/api/v1/keys", headers=admin_headers)).json()["items"]
    assert next(k for k in keys if k["id"] == mine["id"])["storage_chat"] == ""

    # upload with a key that pins a chat → queue payload carries it
    resp = await client.post(
        "/api/v1/keys",
        json={"name": "chan-key2", "scopes": "read,write", "storage_chat": "-100999"},
        headers=admin_headers,
    )
    raw2 = resp.json()["key"]
    up = await client.post(
        "/api/v1/files/upload/session",
        json={"name": "chan.bin", "size": 11},
        headers={"X-API-Key": raw2},
    )
    assert up.status_code == 200, up.text
    sid = up.json()["session_id"]
    done = await client.patch(
        f"/api/v1/files/upload/session/{sid}",
        content=b"hello world",
        headers={"X-API-Key": raw2, "X-Offset": "0"},
    )
    assert done.status_code == 200 and done.json().get("completed"), done.text
    fid = done.json()["file_id"]
    from app.core.state import get_db as _get_db

    row = await (await _get_db()).fetch_one(
        "SELECT payload FROM jobs WHERE kind='upload' ORDER BY seq DESC LIMIT 1"
    )
    import json as _json

    assert _json.loads(row["payload"])["storage_chat"] == "-100999"


async def test_storage_channels_report(client, admin_headers, api_key):
    """Storage-channels report: per-channel file counts, sizes and mime groups."""
    from app.core.state import get_db as gdb

    db = await gdb()
    # key with dedicated channel + one file in it, one file on system default
    r = await client.post("/api/v1/keys", json={"name": "rep-key", "scopes": "read,write", "storage_chat": "-100777"}, headers=admin_headers)
    assert r.status_code == 200
    raw = r.json()["key"]
    for chat, mime, size in (("-100777", "image/png", 100), ("-100777", "image/png", 30), ("-100777", "video/mp4", 500), ("", "text/plain", 7)):
        fid = "f-rep-" + str(abs(hash((chat, mime, size))) % 10**12)
        await db.execute(
            "INSERT INTO files(id, name, size, mime, uploader, source, backend, status, storage_chat, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fid, f"{fid}.bin", size, mime, "t", "api", "telegram", "ready", chat, 1.0),
        )
    # system default channel comes from runtime settings
    r = await client.put("/api/v1/admin/settings", json={"tg_storage_chat": "-100333"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    resp = await client.get("/api/v1/admin/storage-channels", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    by_chat = {i["chat"]: i for i in items}
    # default channel = -100333, file with empty storage_chat lands there
    assert "-100333" in by_chat
    d = by_chat["-100333"]
    assert d["kind"] == "default" and d["is_system_default"] is True
    assert d["files"] == 1 and d["bytes"] == 7
    types = {t["key"]: t for t in d["by_type"]}
    assert types["text"]["count"] == 1 and types["text"]["label"]
    # dedicated channel with 2 images + 1 video
    dd = by_chat["-100777"]
    assert dd["kind"] == "dedicated" and dd["is_system_default"] is False
    assert dd["files"] == 3 and dd["bytes"] == 630
    types = {t["key"]: t for t in dd["by_type"]}
    assert types["image"]["count"] == 2 and types["image"]["bytes"] == 130
    assert types["video"]["count"] == 1 and types["video"]["bytes"] == 500
    # owning key is listed on its channel
    assert any(k["name"] == "rep-key" for k in dd["keys"])
    # channel with no files is still reported via its key
    r = await client.post("/api/v1/keys", json={"name": "empty-chan", "storage_chat": "@emptychan"}, headers=admin_headers)
    assert r.status_code == 200
    resp = await client.get("/api/v1/admin/storage-channels", headers=admin_headers)
    by_chat = {i["chat"]: i for i in resp.json()["items"]}
    assert "@emptychan" in by_chat and by_chat["@emptychan"]["files"] == 0
    # default keys (no channel) are attached to the default entry
    assert all(k["scope"] == "default" for k in by_chat["-100333"]["keys"])
    # cleanup so other tests start clean
    await db.execute("DELETE FROM files WHERE id LIKE 'f-rep-%'")


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
