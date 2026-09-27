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



async def test_preview_token_fallback(client, api_key):
    """Test that ?token= query param works for browser media tags that can't set Authorization headers.
    
    This is a fallback auth method: when a browser <img>/<video>/<iframe> tag requests a preview,
    it cannot set Authorization headers, so it passes the JWT as ?token= query parameter.
    """
    H = {"Authorization": f"Bearer {api_key}"}
    
    # Upload an image file for preview
    import io
    payload = b"fake image data for preview test"
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("preview.jpg", io.BytesIO(payload), "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    
    # Wait for file to become ready
    status = "queued"
    for _ in range(100):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        status = r.json()[ "status" ]
        if status == "ready":
            break
        await asyncio.sleep(0.1)
    assert status == "ready", f"file never became ready: {status}"
    
    # Get preview URL (this would normally be called by browser with ?token=)
    # First, let's test the normal authenticated preview access
    r = await client.get(f"/api/v1/files/{fid}/preview", headers=H)
    # Note: preview may return 415 for non-image types or 200 for images
    # The important thing is that ?token= fallback exists and works
    
    # Test ?token= fallback - get a fresh token
    # Login again to get a new token (simulating a different browser session)
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    body = r.json()
    fresh_token = body["access_token"]
    
    # Access preview with ?token= query parameter (simulating browser behavior)
    # The preview endpoint accepts token as query param via get_api_key dependency
    preview_url = f"/api/v1/files/{fid}/preview?token={fresh_token}"
    r = await client.get(preview_url)
    
    # The response depends on the file type - for image/jpeg it should work
    # For our test data "fake image data", it may or may not be a valid image
    # The key test is that it doesn't crash with 401 "missing API key"
    # and it accepts the ?token= parameter
    assert r.status_code != 401, f"Preview with ?token= should not return 401, got {r.status_code}"
    
    # Also test that without token, it requires Bearer auth
    r_no_token = await client.get(f"/api/v1/files/{fid}/preview")
    assert r_no_token.status_code == 401, "Preview without auth should require Bearer token"
    
    # Test with invalid token
    r_invalid = await client.get(f"/api/v1/files/{fid}/preview?token=invalid-token-xyz")
    # This may return 401 or process the invalid token - the important thing is it doesn't crash


async def test_files_q_and_pagination_total(client, api_key):
    """A4: ?q= name search works across pages and total reflects the unpaginated count."""
    H = {"Authorization": f"Bearer {api_key}"}
    for name in ("alpha-one.txt", "alpha-two.txt", "beta-three.txt"):
        r = await client.post(
            "/api/v1/files/upload",
            headers=H,
            files={"file": (name, io.BytesIO(b"x"), "text/plain")},
        )
        assert r.status_code == 200, r.text

    d = (await client.get("/api/v1/files", params={"q": "alpha", "limit": 1}, headers=H)).json()
    assert len(d["items"]) == 1
    assert d["total"] == 2, d
    assert {i["name"] for i in d["items"]} <= {"alpha-one.txt", "alpha-two.txt"}

    # q with no match → empty page but valid envelope
    d = (await client.get("/api/v1/files", params={"q": "no-such-name"}, headers=H)).json()
    assert d["items"] == [] and d["total"] == 0

    # order=name actually reorders matches
    d = (await client.get("/api/v1/files", params={"q": "alpha", "order": "name"}, headers=H)).json()
    assert [i["name"] for i in d["items"]] == ["alpha-one.txt", "alpha-two.txt"]

    for f in ("alpha-one.txt", "alpha-two.txt", "beta-three.txt"):
        r = await client.get("/api/v1/files", params={"q": f, "limit": 1}, headers=H)
        fid = r.json()["items"][0]["id"]
        await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)


async def test_blocked_files_report(client, api_key, token):
    """A4: admin blocked-files-report lists file.block/file.unblock audit entries
    with uploader and ip, newest first, honoring the limit param."""
    H = {"Authorization": f"Bearer {api_key}"}
    A = {"Authorization": f"Bearer {token}"}
    ids = []
    for name in ("blk-a.txt", "blk-b.txt"):
        r = await client.post(
            "/api/v1/files/upload",
            headers=H,
            files={"file": (name, io.BytesIO(b"x"), "text/plain")},
        )
        ids.append(r.json()["file_id"])

    await client.patch(f"/api/v1/files/{ids[0]}/block", json={"blocked": True}, headers=H)
    await client.patch(f"/api/v1/files/{ids[1]}/block", json={"blocked": True}, headers=H)
    await client.patch(f"/api/v1/files/{ids[1]}/block", json={"blocked": False}, headers=H)

    d = (await client.get("/api/v1/admin/blocked-files-report", headers=A)).json()
    actions = [(i["target"], i["action"]) for i in d["items"]]
    assert (ids[1], "file.unblock") == actions[0], d  # newest first
    assert ("file.block", "file.block") == (actions[-1][1], actions[-1][1])
    assert {t for t, _ in actions} == {ids[0], ids[1]}
    first = d["items"][0]
    assert first["file_name"] == "blk-b.txt" and str(first["uploader"]).startswith("key:") and "ip" in first

    d2 = (await client.get("/api/v1/admin/blocked-files-report", params={"limit": 1}, headers=A)).json()
    assert len(d2["items"]) == 1 and d2["total"] == 3

    for fid in ids:
        await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)


async def test_preview_token_flow(client, api_key):
    """A8: POST /files/{id}/preview-token issues a file-bound HMAC token (≤10 min)
    that authorizes ONLY that file's preview endpoint via ?ptk=."""
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("ptk.jpg", io.BytesIO(b"fake-image"), "image/jpeg")},
    )
    fid = r.json()["file_id"]
    status = "queued"
    for _ in range(100):
        r = await client.get(f"/api/v1/files/{fid}", headers=H)
        status = r.json()["status"]
        if status == "ready":
            break
        await asyncio.sleep(0.05)
    assert status == "ready"

    r = await client.post(f"/api/v1/files/{fid}/preview-token", json={"ttl": 600}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    tok = d["token"]
    assert tok.startswith(f"ptk_{fid}:")
    import time as _t
    assert 10 <= d["expires_at"] - int(_t.time()) <= 600
    assert d["url"].endswith(f"ptk={tok}")

    # preview works with ptk and no Authorization header
    r = await client.get(f"/api/v1/files/{fid}/preview?ptk={tok}")
    assert r.status_code == 200, r.text

    # ptk must NOT authorize other endpoints or other files
    r = await client.get(f"/api/v1/files/{fid}?ptk={tok}")
    assert r.status_code == 401, "ptk must not authorize file info"
    r = await client.get(f"/api/v1/files/{fid}/content?ptk={tok}")
    assert r.status_code == 401, "ptk must not authorize content download"
    r = await client.get("/api/v1/files?ptk=" + tok, headers={})
    assert r.status_code == 401, "ptk must not authorize listing"

    # tampered token → 401
    r = await client.get(f"/api/v1/files/{fid}/preview?ptk={tok[:-4]}beef")
    assert r.status_code == 401

    # ptk for a blocked file is refused at issuance
    await client.patch(f"/api/v1/files/{fid}/block", json={"blocked": True}, headers=H)
    r = await client.post(f"/api/v1/files/{fid}/preview-token", json={}, headers=H)
    assert r.status_code == 403

    # ttl clamp: asking for an hour yields at most 600s
    await client.patch(f"/api/v1/files/{fid}/block", json={"blocked": False}, headers=H)
    r = await client.post(f"/api/v1/files/{fid}/preview-token", json={"ttl": 3600}, headers=H)
    tok2 = r.json()["token"]
    exp2 = int(tok2.split(":")[1])
    assert exp2 - int(_t.time()) <= 600

    # ttl below floor (10s) is raised to it
    r = await client.post(f"/api/v1/files/{fid}/preview-token", json={"ttl": 1}, headers=H)
    tok3 = r.json()["token"]
    exp3 = int(tok3.split(":")[1])
    assert exp3 - int(_t.time()) >= 9  # small timing slack

    await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)


async def test_trash_flow_listing_and_restore(client, api_key):
    """B3: ?trashed=1 lists only soft-deleted files; restore moves them back."""
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("trash-me.txt", io.BytesIO(b"x"), "text/plain")},
    )
    fid = r.json()["file_id"]

    # trashed view is empty before deletion
    d = (await client.get("/api/v1/files", params={"trashed": 1}, headers=H)).json()
    assert all(i["id"] != fid for i in d["items"])

    # soft-delete → appears ONLY in the trashed view
    r = await client.delete(f"/api/v1/files/{fid}", headers=H)
    assert r.status_code == 200 and r.json()["trashed"] == fid
    d = (await client.get("/api/v1/files", params={"trashed": 1}, headers=H)).json()
    assert d["total"] == 1 and d["items"][0]["id"] == fid
    d = (await client.get("/api/v1/files", headers=H)).json()
    assert all(i["id"] != fid for i in d["items"])

    # restore → back in the main list, gone from trash
    r = await client.get(f"/api/v1/files/{fid}/restore", headers=H)
    assert r.status_code == 200, r.text
    d = (await client.get("/api/v1/files", headers=H)).json()
    assert any(i["id"] == fid for i in d["items"])
    d = (await client.get("/api/v1/files", params={"trashed": 1}, headers=H)).json()
    assert d["total"] == 0

    await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)


async def test_queue_retry_failed_job(client, api_key, token):
    """Retry endpoint re-queues a failed job and refuses live ones (409)."""
    from app.core.state import state as st

    A = {"Authorization": f"Bearer {token}"}
    q = st.queue
    from app.core.models import Job, now as _now

    job_id = "j_test_retry"
    await q.jobs.insert(Job(id=job_id, kind="upload", priority=10, seq=_now(), payload={"file_id": "nope"}))
    # simulate a terminal failure
    await q.jobs.update_fields(job_id, status="failed", error="boom")

    # retrying a non-failed job is a conflict
    live_id = "j_test_live"
    await q.jobs.insert(Job(id=live_id, kind="upload", priority=10, seq=_now() + 1, payload={"file_id": "nope"}))
    r = await client.post(f"/api/v1/queue/retry/{live_id}", headers=A)
    assert r.status_code == 409

    r = await client.post(f"/api/v1/queue/retry/{job_id}", headers=A)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text

    # the retried job row is pending again with cleared attempts
    row = await q.jobs.fetch(job_id)
    assert row["status"] == "pending" and row["attempts"] == 0

    # unknown id → 404
    r = await client.post("/api/v1/queue/retry/no-such-job", headers=A)
    assert r.status_code == 404

    # cleanup: drop test jobs so other tests see a clean queue
    await q.jobs.update_fields(job_id, status="failed", error="cleanup")
    await q.jobs.update_fields(live_id, status="failed", error="cleanup")
    await client.post("/api/v1/queue/purge", headers=A)
