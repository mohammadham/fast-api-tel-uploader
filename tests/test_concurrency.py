"""Concurrency test: parallel uploads + downloads through the full stack."""
from __future__ import annotations

import asyncio
import io

from app.core.state import get_db


async def test_parallel_uploads_all_ready(client, api_key):
    H = {"Authorization": f"Bearer {api_key}"}
    n = 6
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/files/upload",
                headers=H,
                files={"file": (f"par{i}.bin", io.BytesIO(bytes([i]) * 2048), "application/octet-stream")},
            )
            for i in range(n)
        ]
    )
    assert all(r.status_code == 200 for r in responses)
    fids = [r.json()["file_id"] for r in responses]

    db = await get_db()
    ready = set()
    for _ in range(400):
        for fid in fids:
            if fid in ready:
                continue
            row = await db.fetch_one("SELECT status FROM files WHERE id=?", (fid,))
            if row and row["status"] == "ready":
                ready.add(fid)
        if len(ready) == n:
            break
        await asyncio.sleep(0.05)
    assert len(ready) == n, f"only {len(ready)}/{n} became ready"


async def test_rate_limit_kicks_in(client, token):
    """rpm=2 key gets 429 after 2 requests."""
    H = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/api/v1/keys",
        headers=H,
        json={"name": "tiny", "scopes": "read,write", "rpm": 2},
    )
    key = r.json()["key"]
    KH = {"Authorization": f"Bearer {key}"}
    codes = []
    for _ in range(6):
        rr = await client.get("/api/v1/files", headers=KH)
        codes.append(rr.status_code)
    assert 429 in codes, f"expected 429 among {codes}"
