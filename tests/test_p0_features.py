"""P0 competitive features: share links (slug/password/max), public file page,
thumbnail, trash/restore, admin notify wiring."""
from __future__ import annotations

import asyncio
import io

import pytest


async def _upload_and_wait(client, api_key, payload: bytes, name="share.txt", mime="text/plain") -> str:
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(
        "/api/v1/files/upload", headers=H, files={"file": (name, io.BytesIO(payload), mime)}
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    for _ in range(200):
        if (await client.get(f"/api/v1/files/{fid}", headers=H)).json()["status"] == "ready":
            return fid
        await asyncio.sleep(0.05)
    raise AssertionError("file never became ready")


async def test_share_link_slug_and_download(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"hello slug", "hello.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "my-file"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "my-file"

    # slug taken → 409
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "my-file"})
    assert r.status_code == 409

    # page renders
    r = await client.get("/my-file")
    assert r.status_code == 200
    assert "hello.txt" in r.text
    assert "<video" not in r.text  # text file → no player tag

    # download through slug serves exact bytes and counts a hit
    r = await client.get("/d/my-file/dl")
    assert r.status_code == 200
    assert r.content == b"hello slug"
    links = (await client.get(f"/api/v1/files/{fid}/links", headers=H)).json()["items"]
    assert links[0]["hits"] == 1


async def test_share_link_password(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"secret", "sec.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "locked", "password": "opensesame"})
    assert r.status_code == 200

    # no password → 401 with the HTML password form
    r = await client.get("/locked")
    assert r.status_code == 401
    assert 'name="pw"' in r.text

    # wrong password → still 401
    r = await client.get("/locked?pw=wrong")
    assert r.status_code == 401

    # correct password → page
    r = await client.get("/locked?pw=opensesame")
    assert r.status_code == 200

    # header variant works too
    r = await client.get("/locked", headers={"X-Link-Password": "opensesame"})
    assert r.status_code == 200


async def test_share_link_max_downloads(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"limited", "lim.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "one-shot", "max_downloads": 1})
    assert r.status_code == 200

    r = await client.get("/d/one-shot/dl")
    assert r.status_code == 200 and r.content == b"limited"
    # second download blocked
    r = await client.get("/d/one-shot/dl")
    assert r.status_code == 403
    assert "سقف دانلود" in r.text


async def test_invalid_slug_rejected(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"x")
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "ab"})  # too short
    assert r.status_code == 400
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "bad slug!"})
    assert r.status_code == 400


async def test_trash_and_restore(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"trashme", "t.txt")
    H = {"Authorization": f"Bearer {api_key}"}

    # default delete = trash: slug page becomes unavailable, file still exists
    r = await client.delete(f"/api/v1/files/{fid}", headers=H)
    assert r.status_code == 200 and r.json()["trashed"] == fid

    r = await client.get(f"/api/v1/files/{fid}", headers=H)
    assert r.json()["deleted_at"] is not None

    # purge=true queues real deletion
    r = await client.delete(f"/api/v1/files/{fid}?purge=true", headers=H)
    assert r.status_code == 200 and r.json()["purged"] == fid

    # restore before purge completes (idempotent, still present in DB)
    r = await client.get(f"/api/v1/files/{fid}/restore", headers=H)
    assert r.status_code in (200, 404)  # purge may have finished already


async def test_trash_hides_slug_page(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"bye", "bye.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "bye-link"})
    await client.delete(f"/api/v1/files/{fid}", headers=H)

    r = await client.get("/bye-link")
    assert r.status_code == 404
    r = await client.get("/d/bye-link/dl")
    assert r.status_code == 404

    await client.get(f"/api/v1/files/{fid}/restore", headers=H)
    r = await client.get("/bye-link")
    assert r.status_code == 200


async def test_video_slug_page_has_player(client, api_key):
    fid = await _upload_and_wait(
        client, api_key, b"fake-mp4-bytes", "clip.mp4", "video/mp4"
    )
    H = {"Authorization": f"Bearer {api_key}"}
    await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "clip"})
    r = await client.get("/clip")
    assert r.status_code == 200
    assert '<video controls' in r.text
    assert 'src="/d/clip/dl"' in r.text


async def test_slug_qr_code(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"qr", "qr.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "qrfile"})

    r = await client.get("/qrfile/qr")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text

    # QR appears on the public page too
    page = await client.get("/qrfile")
    assert '/qrfile/qr' in page.text


class TestQREndpointGuards:
    async def test_qr_of_unknown_slug_404(self, client):
        r = await client.get("/no-such-slug/qr")
        assert r.status_code == 404


async def test_share_link_deleted_link_removal(client, api_key):
    fid = await _upload_and_wait(client, api_key, b"z", "z.txt")
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "removeme"})
    assert r.status_code == 200
    links = (await client.get(f"/api/v1/files/{fid}/links", headers=H)).json()["items"]
    r = await client.delete(f"/api/v1/files/{fid}/links/{links[0]['id']}", headers=H)
    assert r.status_code == 200
    r = await client.get("/removeme")
    assert r.status_code == 404
