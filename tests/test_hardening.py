"""Hardening round tests: security headers, logout blacklist, audit trail,
resumable-upload validation, targeted account ops, round-robin pool,
permanent-error auto-disable, limiter GC, byte accounting."""
from __future__ import annotations

import asyncio
import io
import time

import pytest


async def test_security_headers_present(client, token):
    r = await client.get("/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["cache-control"] == "no-store"  # API responses are never cached


async def test_logout_blacklists_refresh_token(client):
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    body = r.json()
    refresh = body["refresh_token"]

    # refresh works before logout
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200

    # logout with the access token + refresh token
    r = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {body['access_token']}"},
        json={"refresh_token": refresh},
    )
    assert r.status_code == 200

    # same refresh token must now be rejected
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    assert "revoked" in r.json()["detail"]


async def test_audit_trail_captures_login_fail_and_success(client, token):
    # a failed login (401) must be audited
    await client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})

    H = {"Authorization": f"Bearer {token}"}
    r = await client.get("/api/v1/admin/audit", headers=H)
    actions = [item["action"] for item in r.json()["items"]]
    assert "auth.login.fail" in actions
    assert "auth.login.ok" in actions  # the token fixture logged in successfully


async def test_audit_covers_key_and_file_actions(client, token, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("audit.txt", io.BytesIO(b"x"), "text/plain")},
    )
    fid = r.json()["file_id"]
    for _ in range(200):
        if (await client.get(f"/api/v1/files/{fid}", headers=H)).json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    await client.post(f"/api/v1/files/{fid}/link", headers=H, json={"ttl": 60})
    await client.delete(f"/api/v1/files/{fid}", headers=H)

    r = await client.get("/api/v1/admin/audit", headers={"Authorization": f"Bearer {token}"})
    actions = [item["action"] for item in r.json()["items"]]
    for expected in ("apikey.create", "file.upload", "link.create", "file.trash"):
        assert expected in actions, f"missing audit action {expected}"


async def test_resumable_upload_rejects_oversize_chunks(client, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post("/api/v1/files/upload/session", headers=H, json={"name": "ov.bin", "size": 1000})
    sid = r.json()["session_id"]
    # client tries to push more than the declared size
    r = await client.request(
        "PATCH", f"/api/v1/files/upload/session/{sid}", headers={**H, "X-Offset": "0"}, content=b"Z" * 2000
    )
    assert r.status_code == 413


async def test_resumable_upload_rejects_bad_offset_header(client, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post("/api/v1/files/upload/session", headers=H, json={"name": "bad.bin", "size": 100})
    sid = r.json()["session_id"]
    r = await client.request(
        "PATCH", f"/api/v1/files/upload/session/{sid}", headers={**H, "X-Offset": "abc"}, content=b"zz"
    )
    assert r.status_code == 400


async def test_account_test_and_reset_target_exact_account(client, token):
    H = {"Authorization": f"Bearer {token}"}
    items = (await client.get("/api/v1/accounts", headers=H)).json()["items"]
    target = items[0]["id"]

    r = await client.post(f"/api/v1/accounts/{target}/test", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["account_id"] == target  # probed exactly this account, not a random one

    r = await client.post(f"/api/v1/accounts/{target}/reset", headers=H)
    assert r.status_code == 200
    assert r.json()["reconnected"] is True


async def test_round_robin_spreads_load_across_pool(client, token):
    from app.core.models import AccountRepo
    from app.core.security import encrypt_str
    from app.core.state import state

    H = {"Authorization": f"Bearer {token}"}
    # add two more fake accounts → pool of 3
    for i in range(2):
        acc_id = await AccountRepo(state.db).create(f"rr-{i}", f"+98912000000{i}", "")
        await AccountRepo(state.db).set_session(acc_id, encrypt_str(f"fake-session::rr{i}"), "ready")
        await state.manager.refresh_one_account(acc_id)

    counts: dict[str, int] = {}
    for _ in range(9):
        borrowed = await state.manager.acquire("acc")
        counts[borrowed.key] = counts.get(borrowed.key, 0) + 1
        await state.manager.release(borrowed.key, None)

    assert len(counts) == 3, f"round-robin ignored some accounts: {counts}"
    for key, n in counts.items():
        assert n >= 2, f"unbalanced distribution: {counts}"


async def test_permanent_error_disables_account(client, token):
    from app.core.models import AccountRepo
    from app.core.state import state
    from app.tg.base import SendFailure

    H = {"Authorization": f"Bearer {token}"}
    items = (await client.get("/api/v1/accounts", headers=H)).json()["items"]
    acc_id = items[0]["id"]

    await state.manager.release(f"acc:{acc_id}", SendFailure("AuthKeyUnregistered: session revoked"))

    row = await AccountRepo(state.db).get(acc_id)
    assert row["enabled"] == 0
    assert row["status"] == "unauthorized"
    assert "AuthKeyUnregistered" in row["last_error"]

    # and it disappears from the pool immediately (it was the only account → empty pool)
    from app.tg.manager import NoBackendAvailable

    with pytest.raises(NoBackendAvailable):
        await state.manager.acquire("acc", timeout=1.0)


async def test_rate_limiter_gc_removes_idle_buckets(client):
    from app.core.rate_limit import limiter

    # fabricate an ancient bucket as if some IP hit us hours ago
    limiter._buckets["login:1.2.3.4"] = limiter._buckets.get(
        "login:1.2.3.4"
    ) or __import__("app.core.rate_limit", fromlist=["TokenBucket"]).TokenBucket(capacity=1, refill_per_sec=0.01)
    stale = limiter._buckets["login:1.2.3.4"]
    stale.last_seen = time.monotonic() - 10_000

    limiter._last_gc = time.monotonic() - limiter.GC_INTERVAL - 1  # force sweep on next allow()
    await limiter.allow("login:5.6.7.8", capacity=5, per_minute=5)

    assert "login:1.2.3.4" not in limiter._buckets  # idle bucket collected
    assert "login:5.6.7.8" in limiter._buckets


async def test_served_bytes_counted_per_account(client, api_key, token):
    H = {"Authorization": f"Bearer {api_key}"}
    payload = b"B" * 4096
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("bytes.txt", io.BytesIO(payload), "text/plain")},
    )
    fid = r.json()["file_id"]
    for _ in range(200):
        if (await client.get(f"/api/v1/files/{fid}", headers=H)).json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)

    r = await client.get(f"/api/v1/files/{fid}/content", headers=H)
    assert r.status_code == 200
    assert r.content == payload

    # background task counted the bytes against the account that served them
    items = (await client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {token}"})).json()["items"]
    assert any(a["bytes_down"] >= len(payload) for a in items)
