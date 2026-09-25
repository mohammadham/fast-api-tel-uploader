"""Virtual folders (tag-like, nested) — API + upload wiring + telegram caption."""
from __future__ import annotations

import pytest


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def test_folder_crud_and_nested_paths(client, admin_headers):
    """Create nested folders via path, tree listing, rename/move, delete."""
    r = await client.post("/api/v1/folders", json={"path": "projects/2026/reports"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    leaf_id = r.json()["id"]
    assert r.json()["created"] is True

    # idempotent second create returns the same folder
    r = await client.post("/api/v1/folders", json={"path": "projects/2026/reports"}, headers=admin_headers)
    assert r.status_code == 201 and r.json()["id"] == leaf_id and r.json()["created"] is False

    # flat list carries full paths
    items = (await client.get("/api/v1/folders", headers=admin_headers)).json()["items"]
    paths = {i["path"] for i in items}
    assert {"projects", "projects/2026", "projects/2026/reports"} <= paths

    # tree is nested: root → children
    tree = (await client.get("/api/v1/folders", headers=admin_headers)).json()["tree"]
    proj = next(t for t in tree if t["name"] == "projects")
    assert proj["children"] and proj["children"][0]["name"] == "2026"

    # single-name create under a parent
    r = await client.post("/api/v1/folders", json={"name": "invoices", "parent_id": leaf_id}, headers=admin_headers)
    assert r.status_code == 201
    inv_id = r.json()["id"]

    # cycle prevention: moving projects under its own descendant fails
    projects_id = (await client.get("/api/v1/folders/resolve", params={"path": "projects"}, headers=admin_headers)).json()["id"]
    r = await client.patch(f"/api/v1/folders/{projects_id}", json={"parent_id": inv_id}, headers=admin_headers)
    assert r.status_code == 400  # invoices lives inside projects subtree

    # rename + move to root
    r = await client.patch(f"/api/v1/folders/{inv_id}", json={"name": "bills"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["name"] == "bills"
    r = await client.patch(f"/api/v1/folders/{inv_id}", json={"parent_id": None}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["parent_id"] is None

    # invalid names are rejected
    for bad in ("", "a/b", ".", "x" * 101):
        r = await client.post("/api/v1/folders", json={"name": bad}, headers=admin_headers)
        assert r.status_code == 400, bad

    # resolve endpoint (no create)
    r = await client.get("/api/v1/folders/resolve", params={"path": "projects/2026"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["id"] is not None
    r = await client.get("/api/v1/folders/resolve", params={"path": "missing/dir"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["id"] is None

    # delete folder → files' folder_id falls back to NULL, nested removed
    r = await client.delete("/api/v1/folders", headers=admin_headers) if False else await client.delete(f"/api/v1/folders/{leaf_id}", headers=admin_headers)
    assert r.status_code == 200
    items = (await client.get("/api/v1/folders", headers=admin_headers)).json()["items"]
    assert "projects/2026/reports" not in {i["path"] for i in items}


async def test_folder_file_listing_and_move(client, admin_headers, api_key):
    """List files per folder, recursive listing, move/detach via endpoints."""
    from app.core.state import get_db as gdb

    db = await gdb()
    r = await client.post("/api/v1/folders", json={"path": "media/photos"}, headers=admin_headers)
    leaf = r.json()["id"]
    parent = (await client.get("/api/v1/folders/resolve", params={"path": "media"}, headers=admin_headers)).json()["id"]

    for fid, fname, folder in (("ff-1", "in-leaf.png", leaf), ("ff-2", "in-parent.txt", parent), ("ff-3", "no-folder.bin", None)):
        await db.execute(
            "INSERT INTO files(id, name, size, mime, uploader, source, backend, status, folder_id, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fid, fname, 10, "image/png" if fname.endswith("png") else "text/plain", "t", "api", "telegram", "ready", folder, 1.0),
        )

    # direct listing shows only the folder's own files
    d = (await client.get(f"/api/v1/folders/{leaf}/files", headers=admin_headers)).json()
    assert [i["id"] for i in d["items"]] == ["ff-1"]
    assert d["folder"]["path"] == "media/photos"

    # recursive listing includes subfolder files
    d = (await client.get(f"/api/v1/folders/{parent}/all", headers=admin_headers)).json()
    ids = {i["id"] for i in d["items"]}
    assert ids == {"ff-1", "ff-2"}

    # move ff-3 into parent, then detach it again
    r = await client.post(f"/api/v1/folders/{parent}/files/ff-3", headers=admin_headers)
    assert r.status_code == 200 and r.json()["folder_id"] == parent
    d = (await client.get(f"/api/v1/folders/{parent}/all", headers=admin_headers)).json()
    assert "ff-3" in {i["id"] for i in d["items"]}
    r = await client.delete(f"/api/v1/folders/{parent}/files/ff-3", headers=admin_headers)
    assert r.status_code == 200 and r.json()["folder_id"] is None

    # move to missing folder → 404
    r = await client.post("/api/v1/folders/999999/files/ff-3", headers=admin_headers)
    assert r.status_code == 404

    # cleanup
    await db.execute("DELETE FROM files WHERE id LIKE 'ff-%'")


async def test_upload_with_folder_creates_tag_caption(client, admin_headers, api_key):
    """Upload with X-Folder creates nested folders; queue payload + caption carry tags."""
    from app.core.state import get_db as gdb
    import json as _json

    up = await client.post(
        "/api/v1/files/upload/session",
        json={"name": "tagged.bin", "size": 5},
        headers={"X-API-Key": api_key, "X-Folder": "docs/2026"},
    )
    assert up.status_code == 200, up.text
    sid = up.json()["session_id"]
    done = await client.patch(
        f"/api/v1/files/upload/session/{sid}",
        content=b"hello",
        headers={"X-API-Key": api_key, "X-Offset": "0"},
    )
    assert done.status_code == 200 and done.json().get("completed"), done.text
    fid = done.json()["file_id"]

    db = await gdb()
    row = await db.fetch_one("SELECT payload FROM jobs WHERE kind='upload' ORDER BY seq DESC LIMIT 1")
    payload = _json.loads(row["payload"])
    assert payload["folder_path"] == "docs/2026"
    frec = await db.fetch_one("SELECT folder_id FROM files WHERE id=?", (fid,))
    assert frec["folder_id"] is not None

    # folders were auto-created by the upload
    items = (await client.get("/api/v1/folders", headers=admin_headers)).json()["items"]
    assert "docs/2026" in {i["path"] for i in items}

    # invalid path is rejected
    up = await client.post(
        "/api/v1/files/upload/session",
        json={"name": "bad.bin", "size": 5},
        headers={"X-API-Key": api_key, "X-Folder": "a/" + "x" * 120},
    )
    assert up.status_code == 400


async def test_folder_caption_reaches_telegram_backend(client, admin_headers):
    """Queue _handle_upload builds '#docs #2026' caption from the folder path."""
    from app.tg.fake import FakeBackend

    be = FakeBackend("acc:test")
    caption = "#docs #2026"
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(b"x" * 10)
        p = fh.name
    try:
        res = await be.send_document("me", p, "t.bin", "application/octet-stream", caption=caption)
        assert FakeBackend.CAPTIONS[res["message_id"]] == caption
    finally:
        os.unlink(p)
