"""Eitaa multi-backend storage: routing, upload/download roundtrip, admin CRUD."""
from __future__ import annotations

import io

import pytest


async def _key(client, token, backend=""):
    r = await client.post(
        "/api/v1/keys",
        json={"name": "k", "scopes": "read,write", "rpm": 600, "backend": backend},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["key"]


async def _add_eitaa(client, token, chat="mychan"):
    r = await client.post(
        "/api/v1/eitaa",
        json={"token": "eitaa-token-1234567890", "chat_id": chat, "label": "eit1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _upload_wait(client, api_key, backend_expected: str) -> str:
    r = await client.post(
        "/api/v1/files/upload",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("e.txt", io.BytesIO(b"eitaa-hello"), "text/plain")},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    for _ in range(60):
        info = await client.get(f"/api/v1/files/{fid}", headers={"Authorization": f"Bearer {api_key}"})
        if info.json().get("status") == "ready":
            got = info.json()["backend"] or ""
            assert got in (backend_expected, "", "telegram") if backend_expected == "" else got == backend_expected
            return fid
        await __import__("asyncio").sleep(0.05)
    pytest.fail(f"upload never became ready: {info.text}")


@pytest.mark.asyncio
async def test_key_backend_validation(client, token):
    r = await client.post(
        "/api/v1/keys", json={"name": "x", "backend": "nope"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_default_backend_telegram_no_eitaa_needed(client, token, api_key):
    """backend='' behaves exactly as before → telegram pool."""
    fid = await _upload_wait(client, api_key, "")
    assert fid.startswith("f_")


@pytest.mark.asyncio
async def test_eitaa_upload_routes_to_eitaa_pool(client, token):
    """Key pinned to eitaa → upload goes through eit: backend (fake-typed eitaa)."""
    await _add_eitaa(client, token)
    key = await _key(client, token, backend="eitaa")
    fid = await _upload_wait(client, key, "eitaa")
    info = (await client.get(f"/api/v1/files/{fid}", headers={"Authorization": f"Bearer {key}"})).json()
    # message stored under the eitaa chat id in the shared fake store
    from app.tg.fake import FakeBackend

    mid = info["parts"][0]["message_id"]
    assert ("mychan", mid) in FakeBackend.STORE


@pytest.mark.asyncio
async def test_eitaa_key_without_eitaa_backend_fails_loud(client, token):
    key = await _key(client, token, backend="eitaa")
    r = await client.post(
        "/api/v1/files/upload",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert r.status_code == 200  # queued
    import asyncio

    fid = r.json()["file_id"]
    # job retries with backoff (max_retries=5); poll until terminal failure
    deadline = __import__("time").time() + 120
    while __import__("time").time() < deadline:
        info = await client.get(f"/api/v1/files/{fid}", headers={"Authorization": f"Bearer {key}"})
        if info.json().get("status") == "failed":
            assert "no telegram backend configured" in info.json().get("error", "")
            return
        await asyncio.sleep(0.3)
    pytest.fail("upload should fail without any eitaa backend")


@pytest.mark.asyncio
async def test_eitaa_crud_toggle_delete(client, token):
    eid = await _add_eitaa(client, token)
    r = await client.get("/api/v1/eitaa", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    r = await client.post(f"/api/v1/eitaa/{eid}/toggle", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["enabled"] is False
    r = await client.post(f"/api/v1/eitaa/{eid}/toggle", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["enabled"] is True
    r = await client.post(f"/api/v1/eitaa/{eid}/test", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["ok"] is True
    r = await client.delete(f"/api/v1/eitaa/{eid}", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["ok"] is True
    r = await client.get("/api/v1/eitaa", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_eitaa_download_roundtrip(client, token):
    """Eitaa-stored file streams back through the same eit: backend."""
    await _add_eitaa(client, token)
    key = await _key(client, token, backend="eitaa")
    fid = await _upload_wait(client, key, "eitaa")
    r = await client.get(f"/api/v1/files/{fid}/content", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.content == b"eitaa-hello"


@pytest.mark.asyncio
async def test_telegram_file_untouched_by_eitaa_pool(client, token, api_key):
    """Telegram key still downloads after an eitaa backend exists (no cross-routing)."""
    await _add_eitaa(client, token)
    fid = await _upload_wait(client, api_key, "")
    r = await client.get(f"/api/v1/files/{fid}/content", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_key_patch_backend(client, token):
    key = await _key(client, token)
    info = await _key_id(client, token, key)
    r = await client.patch(
        f"/api/v1/keys/{info}",
        json={"backend": "eitaa"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    rows = (await client.get("/api/v1/keys", headers={"Authorization": f"Bearer {token}"})).json()["items"]
    assert [row for row in rows if raw_matches(row, key)][0]["backend"] == "eitaa"


def raw_matches(row, raw):
    return raw.startswith(row["key_prefix"])


async def _key_id(client, token, raw):
    rows = (await client.get("/api/v1/keys", headers={"Authorization": f"Bearer {token}"})).json()["items"]
    for row in rows:
        if raw.startswith(row["key_prefix"]):
            return row["id"]
    raise AssertionError("key row not found")
