"""Bulk zip download: signed URL, streamed archive content, failure modes."""
import io
import zipfile

import pytest


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


async def _upload_ready(client, headers, name, body):
    r = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": (name, io.BytesIO(body), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    import asyncio

    for _ in range(80):
        rec = (await client.get(f"/api/v1/files/{fid}", headers=headers)).json()
        if rec.get("status") == "ready":
            return fid
        assert rec.get("status") != "failed", rec
        await asyncio.sleep(0.05)
    raise AssertionError("file never became ready")


@pytest.mark.asyncio
async def test_bulk_zip_roundtrip_content_and_signature(client, api_key, token):
    """mint -> signed GET -> valid zip with both files (unique names)."""
    f1 = await _upload_ready(client, _H(api_key), "zip-a.txt", b"hello-zip-A")
    f2 = await _upload_ready(client, _H(api_key), "zip-a.txt", b"second-B-with-same-name")

    r = await client.post("/api/v1/files/bulk-zip", headers=_H(api_key), json={"file_ids": [f1, f2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == 2
    assert body["bytes"] == len(b"hello-zip-A") + len(b"second-B-with-same-name")
    assert body["skipped"] == []

    # the signed URL needs no auth
    r = await client.get(body["url"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    infos = zf.infolist()
    assert len(infos) == 2
    # duplicate base name → second entry suffixed, both readable
    assert {i.filename for i in infos} == {"zip-a.txt", "zip-a-1.txt"}
    contents = set()
    for i in infos:
        with zf.open(i) as fp:
            contents.add(fp.read())
    assert b"hello-zip-A" in contents and b"second-B-with-same-name" in contents

    # tampered signature is rejected
    bad = body["url"].replace("sig=", "sig=0000")
    r = await client.get(bad)
    assert r.status_code == 403

    # expired exp is rejected
    import re
    import time as _t

    old_exp = int(_t.time()) - 10
    ids = re.search(r"ids=([^&]+)", body["url"]).group(1)
    from app.api.files import _zip_sig

    stale = f"/api/v1/files/bulk-zip?ids={ids}&exp={old_exp}&sig={_zip_sig(ids, old_exp)}"
    r = await client.get(stale)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bulk_zip_skips_unusable_and_empty_selection_fails(client, api_key, token):
    r = await client.post("/api/v1/files/bulk-zip", headers=_H(api_key), json={"file_ids": ["f_missing"]})
    assert r.status_code == 400  # nothing downloadable

    r = await client.post("/api/v1/files/bulk-zip", headers=_H(api_key), json={"file_ids": []})
    assert r.status_code == 400

    # a real file mixed with a bogus one: bogus is skipped, real one zips
    good = await _upload_ready(client, _H(api_key), "zip-good.bin", b"good-data")
    r = await client.post("/api/v1/files/bulk-zip", headers=_H(api_key), json={"file_ids": [good, "f_missing"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == 1
    assert body["skipped"] == [{"file_id": "f_missing", "reason": "not ready or blocked"}]

    r = await client.get(body["url"])
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist() == ["zip-good.bin"]
    assert zf.read("zip-good.bin") == b"good-data"
