import os

import pytest


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


@pytest.mark.asyncio
async def test_cancel_tombstones_session_and_rejects_chunks(client, api_key):
    H = _H(api_key)
    r = await client.post("/api/v1/files/upload/session", headers=H, json={"name": "cancel-me.bin", "size": 10})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    r = await client.request("PATCH", "/api/v1/files/upload/session/" + sid, headers=dict(H, **{"X-Offset": "0"}), content=b"AAAAA")
    assert r.status_code == 200, r.text

    r = await client.delete("/api/v1/files/upload/session/" + sid, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "canceled"

    r = await client.request("PATCH", "/api/v1/files/upload/session/" + sid, headers=dict(H, **{"X-Offset": "5"}), content=b"BBBBB")
    assert r.status_code == 410
    assert "canceled" in r.json()["detail"]

    r = await client.delete("/api/v1/files/upload/session/" + sid, headers=H)
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_cancel_deletes_part_file(client, api_key):
    from app.core.config import get_settings

    H = _H(api_key)
    r = await client.post("/api/v1/files/upload/session", headers=H, json={"name": "cancel-me2.bin", "size": 10})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    r = await client.request("PATCH", "/api/v1/files/upload/session/" + sid, headers=dict(H, **{"X-Offset": "0"}), content=b"12345")
    assert r.status_code == 200, r.text

    part = os.path.join(get_settings().final_tmp_dir(), sid + ".part")
    assert os.path.isfile(part)

    r = await client.delete("/api/v1/files/upload/session/" + sid, headers=H)
    assert r.status_code == 200
    assert not os.path.exists(part)


@pytest.mark.asyncio
async def test_cancel_unknown_session_404(client, api_key):
    r = await client.delete("/api/v1/files/upload/session/us_missing", headers=_H(api_key))
    assert r.status_code == 404
