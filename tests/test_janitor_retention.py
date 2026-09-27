"""Janitor retention sweeps + account-channel cache invalidation."""
from __future__ import annotations

import time

from app.core.models import ApiKeyRepo, now


async def test_janitor_purges_old_revoked_keys_audit_and_tokens(client, token):
    from app.core.state import get_db as gdb
    from app.services.janitor import Janitor

    db = await gdb()
    cut30 = now() - 31 * 86400
    cut90 = now() - 91 * 86400

    # revoked key older than 30d → gone; recent revoked → stays
    await db.execute(
        "INSERT INTO api_keys(name, key_hash, key_prefix, scopes, revoked, created_at)"
        " VALUES('old', 'h1', 'p1', 'read', 1, ?)",
        (cut30,),
    )
    await db.execute(
        "INSERT INTO api_keys(name, key_hash, key_prefix, scopes, revoked, created_at)"
        " VALUES('new', 'h2', 'p2', 'read', 1, ?)",
        (now() - 5 * 86400,),
    )
    # fresh active key with old created_at must NEVER be deleted
    await db.execute(
        "INSERT INTO api_keys(name, key_hash, key_prefix, scopes, revoked, created_at)"
        " VALUES('ancient-active', 'h3', 'p3', 'read', 0, ?)",
        (cut30,),
    )
    await db.execute(
        "INSERT INTO audit_log(ts, actor, action) VALUES(?, 't', 'x')", (cut90,)
    )
    await db.execute(
        "INSERT INTO audit_log(ts, actor, action) VALUES(?, 't', 'x')", (now() - 10,)
    )
    await db.execute(
        "INSERT INTO revoked_tokens(jti, expires_at) VALUES('dead', ?)", (cut90,)
    )
    await db.execute(
        "INSERT INTO revoked_tokens(jti, expires_at) VALUES('fresh', ?)", (now() + 3600,)
    )

    await Janitor(db)._sweep()

    names = {r["name"] for r in await db.fetch_all("SELECT name FROM api_keys")}
    assert "old" not in names and "ancient-active" in names and "new" in names
    audit = [r["ts"] for r in await db.fetch_all("SELECT ts FROM audit_log")]
    assert all(ts > now() - 90 * 86400 for ts in audit)
    tok = {r["jti"] for r in await db.fetch_all("SELECT jti FROM revoked_tokens")}
    assert tok == {"fresh"}


async def test_account_patch_invalidates_storage_channels_cache(client, token):
    """PATCH /accounts/{id} with storage_chat_id must drop the 30s TTL cache."""
    import app.api.admin as admin_mod

    A = {"Authorization": f"Bearer {token}"}
    accounts = (await client.get("/api/v1/accounts", headers=A)).json()["items"]
    acc_id = accounts[0]["id"]

    admin_mod._storage_channels_cache = None
    await client.get("/api/v1/admin/storage-channels", headers=A)  # prime cache
    assert admin_mod._storage_channels_cache  # primed

    r = await client.patch(
        f"/api/v1/accounts/{acc_id}", json={"storage_chat_id": "@acct-chan"}, headers=A
    )
    assert r.status_code == 200, r.text
    assert not admin_mod._storage_channels_cache  # invalidated by the PATCH
