"""The upload worker must always record a correct sha256 for the whole file.

Covers the direct multipart path, the resumable chunked path, a split
(multi-part) upload, and the guarantee that a digest failure never fails a
stored upload.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os

import pytest

from app.queue.queue_manager import sha256_file

HEX = set("0123456789abcdef")


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


async def _wait(client, api_key, fid, tries=200):
    rec = {}
    for _ in range(tries):
        rec = (await client.get(f"/api/v1/files/{fid}", headers=_H(api_key))).json()
        if rec.get("status") in ("ready", "failed", "error"):
            return rec
        await asyncio.sleep(0.05)
    return rec


def test_sha256_file_matches_hashlib_across_chunk_boundary(tmp_path):
    """The chunked reader must produce the same digest as hashlib on the bytes."""
    from app.queue import queue_manager as qm

    payload = bytes(range(256)) * 40_000  # 10.24 MB → crosses several 1 MiB chunks
    p = tmp_path / "big.bin"
    p.write_bytes(payload)
    assert qm.SHA256_CHUNK == 1024 * 1024
    assert sha256_file(str(p)) == hashlib.sha256(payload).hexdigest()

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert sha256_file(str(empty)) == hashlib.sha256(b"").hexdigest()


@pytest.mark.asyncio
async def test_direct_upload_records_sha256(client, api_key):
    payload = b"direct-upload-payload" * 5000
    r = await client.post("/api/v1/files/upload", headers=_H(api_key),
                          files={"file": ("sha-direct.bin", io.BytesIO(payload), "application/octet-stream")})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]

    rec = await _wait(client, api_key, fid)
    assert rec["status"] == "ready", rec
    assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(rec["sha256"]) == 64 and set(rec["sha256"]) <= HEX


@pytest.mark.asyncio
async def test_chunked_resumable_upload_records_whole_file_sha256(client, api_key):
    """Chunks arrive separately — the digest must still cover every byte, in order."""
    payload = bytes(range(256)) * 2000  # 512 KB, three chunks
    H = _H(api_key)

    r = await client.post("/api/v1/files/upload/session", headers=H,
                          json={"name": "sha-chunked.bin", "size": len(payload)})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    step = len(payload) // 3
    fid = None
    for off in range(0, len(payload), step):
        blk = payload[off:off + step]
        r = await client.request("PATCH", f"/api/v1/files/upload/session/{sid}",
                                 headers=dict(H, **{"X-Offset": str(off)}), content=blk)
        assert r.status_code == 200, r.text
        body = r.json()
        # the session finalizes ITSELF once the declared size is reached
        assert body["offset"] == min(off + len(blk), len(payload))
        if body.get("completed"):
            assert body.get("file_id"), "the completing chunk returns the new file_id"
            fid = body["file_id"]
    assert fid, "last chunk must complete the session"

    rec = await _wait(client, api_key, fid)
    assert rec["status"] == "ready", rec
    assert rec["sha256"] == hashlib.sha256(payload).hexdigest(), "digest must cover the whole file"


@pytest.mark.asyncio
async def test_split_upload_records_sha256_of_the_original(client, api_key, token):
    """A split upload sends N parts; the digest is still of the ONE source file."""
    payload = os.urandom(300 * 1024)
    H = _H(api_key)

    before = (await client.get("/api/v1/admin/settings", headers={"Authorization": "Bearer " + token})).json()
    old_split = before.get("split_threshold")
    try:
        # force the split path: threshold well below the payload size
        r = await client.put("/api/v1/admin/settings", headers={"Authorization": "Bearer " + token},
                             json={"split_threshold": 64 * 1024})
        assert r.status_code == 200, r.text

        r = await client.post("/api/v1/files/upload", headers=H,
                              files={"file": ("sha-split.bin", io.BytesIO(payload), "application/octet-stream")})
        assert r.status_code == 200, r.text
        fid = r.json()["file_id"]

        rec = await _wait(client, api_key, fid)
        assert rec["status"] == "ready", rec
        assert len(rec["parts"]) >= 2, f"expected a split upload, got {len(rec['parts'])} part(s)"
        assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
    finally:
        if old_split is not None:
            await client.put("/api/v1/admin/settings", headers={"Authorization": "Bearer " + token},
                             json={"split_threshold": old_split})


@pytest.mark.asyncio
async def test_digest_failure_never_fails_the_upload(client, api_key, monkeypatch):
    """The file is already stored at that point: a broken hash must not 500 the job."""
    from app.queue import queue_manager as qm

    monkeypatch.setattr(qm, "sha256_file", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    payload = b"still-stored-anyway" * 100
    r = await client.post("/api/v1/files/upload", headers=_H(api_key),
                          files={"file": ("sha-boom.bin", io.BytesIO(payload), "application/octet-stream")})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]

    rec = await _wait(client, api_key, fid)
    assert rec["status"] == "ready", f"upload must survive a digest failure: {rec}"
    assert rec["sha256"] == "", "no digest is better than a wrong one"