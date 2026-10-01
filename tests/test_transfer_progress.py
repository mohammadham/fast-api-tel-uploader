"""Live transfer progress: payload updates while transferring + admin endpoint."""
import io

import pytest


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


@pytest.mark.asyncio
async def test_transfer_progress_endpoint_lists_jobs(client, api_key, token):
    H = _H(api_key)

    # upload two files to the default channel
    sids = []
    for name in ("tp-a.bin", "tp-b.bin"):
        r = await client.post("/api/v1/files/upload", headers=H, files={"file": (name, io.BytesIO(b"x" * 64), "application/octet-stream")})
        assert r.status_code == 200, r.text
        sids.append(r.json()["file_id"])

    # bulk-transfer both to another chat
    r = await client.post(
        "/api/v1/files/bulk-transfer",
        headers=H,
        json={"file_ids": sids, "storage_chat": "@target-chan"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["enqueued"]) == set(sids)

    # the admin endpoint must report both jobs
    r = await client.get("/api/v1/queue/transfer-progress", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    got = {i["file_id"] for i in items}
    assert set(sids) <= got
    for i in items:
        if i["file_id"] in sids:
            assert i["target_chat"] == "@target-chan"
            assert i["bytes_total"] == 64
            assert i["pct"] in range(0, 101)
