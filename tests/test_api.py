"""API integration tests with FakeTelegram (no network)."""
from __future__ import annotations

import asyncio
import io
import time

import pytest


async def test_login_flow(client):
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "refresh_token" in body
    # refresh works
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r2.status_code == 200
    # me
    r3 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r3.json()["username"] == "admin"


async def test_key_required(client):
    r = await client.get("/api/v1/files")
    assert r.status_code == 401  # no key
    r = await client.get("/api/v1/files", headers={"Authorization": "Bearer td_bogus"})
    assert r.status_code == 401


async def test_full_upload_download_cycle(client, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    payload = b"hello telegram drive " * 100

    r = await client.post("/api/v1/files/upload", headers=H, files={"file": ("demo.txt", io.BytesIO(payload), "text/plain")})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]

    # wait until processed by queue (upload → ready)
    status = "queued"
    for _ in range(200):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        status = r.json()["status"]
        if status == "ready":
            break
        await asyncio.sleep(0.05)
    assert status == "ready", f"file never became ready: {status}"

    # presigned link
    r = await client.post(f"/api/v1/files/{fid}/link", headers=H, json={"ttl": 120})
    assert r.status_code == 200
    link = r.json()["url"]
    path = link.split("http://test", 1)[-1]

    # public download (full)
    r = await client.get(path)
    assert r.status_code == 200
    assert r.content == payload

    # range download
    r2 = await client.get(path, headers={"Range": "bytes=0-9"})
    assert r2.status_code == 206
    assert r2.content == payload[:10]
    assert r2.headers["content-range"].startswith("bytes 0-9/")

    # suffix range
    r3 = await client.get(path, headers={"Range": "bytes=-5"})
    assert r3.status_code == 206
    assert r3.content == payload[-5:]


async def test_upload_session_resumable(client, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    data = b"Z" * 1024

    r = await client.post("/api/v1/files/upload/session", headers=H, json={"name": "chunky.bin", "size": len(data)})
    assert r.status_code == 200
    sid = r.json()["session_id"]

    # wrong offset → 409
    r = await client.request("PATCH", f"/api/v1/files/upload/session/{sid}", headers={**H, "X-Offset": "50"}, content=b"xx")
    assert r.status_code == 409

    r = await client.request("PATCH", f"/api/v1/files/upload/session/{sid}", headers={**H, "X-Offset": "0"}, content=data)
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is True
    assert body["file_id"].startswith("f_")


async def test_delete_queues_job(client, api_key, token):
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post("/api/v1/files/upload", headers=H, files={"file": ("todelete.bin", io.BytesIO(b"bye"), "application/octet-stream")})
    fid = r.json()["file_id"]
    for _ in range(200):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        if r.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    # purge=true → real deletion job (default is soft-delete/trash)
    r = await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)
    assert r.status_code == 200
    for _ in range(200):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        if r.status_code == 404:
            break
        await asyncio.sleep(0.05)
    assert r.status_code == 404  # gone after delete job processed


async def test_admin_overview_and_audit(client, token):
    H = {"Authorization": f"Bearer {token}"}
    r = await client.get("/api/v1/admin/overview", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert "files" in body and "queue" in body

    r = await client.get("/api/v1/admin/audit", headers=H)
    assert r.status_code == 200

    r = await client.get("/api/v1/admin/metrics", headers=H)
    assert r.status_code == 200
    assert "tgdrive_" in r.text


async def test_fake_account_added_automatically_usable(client, token):
    """In fake mode, login/start completes instantly and pool gets a backend."""
    H = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/v1/accounts/login/start", headers=H, json={"phone": "+989120000000", "label": "tester"})
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    r = await client.get("/api/v1/accounts", headers=H)
    items = r.json()["items"]
    assert any(a["status"] == "ready" for a in items)
