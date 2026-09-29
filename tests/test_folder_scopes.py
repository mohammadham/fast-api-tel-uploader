"""Per-channel folder scoping: two channels can own /same-name trees that never
collide; the global view still lists everything."""
from __future__ import annotations

import pytest

from app.core.models import FolderRepo
from app.core.state import get_db


@pytest.fixture()
async def db():
    return await get_db()


async def test_folder_scopes_are_isolated(db):
    repo = FolderRepo(db)
    # same path in two different channel scopes + one global folder
    a = await repo.resolve_path("reports/2026", create=True, scope="-100AAA")
    b = await repo.resolve_path("reports/2026", create=True, scope="-100BBB")
    g = await repo.resolve_path("reports/2026", create=True, scope="")
    assert a and b and g and len({a, b, g}) == 3, "same path in different scopes must be distinct rows"

    # resolving inside a scope only sees that scope's tree
    assert await repo.resolve_path("reports/2026", create=False, scope="-100AAA") == a
    assert await repo.resolve_path("reports/2026", create=False, scope="-100BBB") == b
    assert await repo.resolve_path("reports/2026", create=False, scope="") == g
    # a scope cannot accidentally resolve another scope's folder
    assert await repo.resolve_path("reports/2026", create=False, scope="-100CCC") is None

    # scoped listing hides other scopes; global view shows everything
    sa = await repo.list(scope="-100AAA")
    assert any(f["id"] == a for f in sa) and not any(f["id"] == b for f in sa)
    sg = await repo.list(scope="")
    ids = {f["id"] for f in sg}
    assert {a, b, g} <= ids

    # cleanup
    for fid in (a, b, g):
        await repo.delete(fid)


async def test_folder_scoped_create_api(client, token):
    """POST /api/v1/folders with scope → scoped tree; GET ?scope= filters."""
    hdr = {"Authorization": f"Bearer {token}"}
    r1 = await client.post("/api/v1/folders", json={"path": "chanA/docs", "scope": "-100AAA"}, headers=hdr)
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/api/v1/folders", json={"path": "chanA/docs", "scope": "-100BBB"}, headers=hdr)
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] != r2.json()["id"]

    la = await client.get("/api/v1/folders", params={"scope": "-100AAA"}, headers=hdr)
    assert la.status_code == 200
    ids_a = {f["id"] for f in la.json()["items"]}
    assert r1.json()["id"] in ids_a and r2.json()["id"] not in ids_a

    lall = await client.get("/api/v1/folders", headers=hdr)
    ids_all = {f["id"] for f in lall.json()["items"]}
    assert {r1.json()["id"], r2.json()["id"]} <= ids_all
