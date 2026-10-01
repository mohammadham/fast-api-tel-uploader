"""Optional source cleanup after a successful transfer (transfer_delete_source)."""
import io

import pytest


def _H(api_key):
    return {"Authorization": "Bearer " + api_key}


async def _upload_ready(client, headers, name, body=b"x" * 64):
    r = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": (name, io.BytesIO(body), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    for _ in range(80):
        rec = (await client.get(f"/api/v1/files/{fid}", headers=headers)).json()
        if rec.get("status") == "ready":
            return fid
        assert rec.get("status") != "failed", rec
        import asyncio

        await asyncio.sleep(0.05)
    raise AssertionError("file never became ready")


async def _put_setting(client, token, value):
    r = await client.put(
        "/api/v1/admin/settings",
        headers={"Authorization": "Bearer " + token},
        json={"transfer_delete_source": value},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_transfer_deletes_source_when_enabled(client, api_key, token):
    """transfer_delete_source=1: after transfer the old FakeTG store entries are gone."""
    await _put_setting(client, token, 1)
    from app.tg.fake import FakeBackend

    try:
        fids = [await _upload_ready(client, _H(api_key), f"src-del-{k}.bin") for k in range(2)]
        from app.core.state import state

        db = state.queue.db
        src_parts = []
        for fid in fids:
            rec = await state.queue.files.get(fid)
            parts = await state.queue.files.parts(fid)
            src_parts.append((parts[0]["message_id"], rec["storage_chat"] or "me"))
        # both live in the FakeTG store before the transfer
        for mid, chat in src_parts:
            assert (chat, mid) in FakeBackend.STORE

        r = await client.post(
            "/api/v1/files/bulk-transfer",
            headers=_H(api_key),
            json={"file_ids": fids, "storage_chat": "@cleanup-chan"},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["enqueued"]) == 2, r.json()

        # wait for both transfer jobs to finish
        for _ in range(120):
            rows = await db.fetch_all(
                "SELECT status FROM jobs WHERE kind='transfer' ORDER BY seq DESC LIMIT 2"
            )
            if rows and all(x["status"] in ("done", "failed") for x in rows):
                break
            import asyncio

            await asyncio.sleep(0.05)
        assert all(x["status"] == "done" for x in rows), rows

        # source copies removed from the fake store
        for mid, chat in src_parts:
            assert (chat, mid) not in FakeBackend.STORE, (chat, mid)
        # files now point at the target channel and stay ready
        for fid in fids:
            rec = await state.queue.files.get(fid)
            assert rec["status"] == "ready"
            assert rec["storage_chat"] == "@cleanup-chan"
    finally:
        await _put_setting(client, token, 0)


@pytest.mark.asyncio
async def test_transfer_keeps_source_when_disabled(client, api_key, token):
    """Default (0): source copies stay in the old chat."""
    from app.tg.fake import FakeBackend

    from app.core.state import state

    fid = await _upload_ready(client, _H(api_key), "src-keep.bin")
    db = state.queue.db
    rec = await state.queue.files.get(fid)
    parts = await state.queue.files.parts(fid)
    mid, chat = parts[0]["message_id"], rec["storage_chat"] or "me"
    assert (chat, mid) in FakeBackend.STORE

    r = await client.post(
        "/api/v1/files/bulk-transfer",
        headers=_H(api_key),
        json={"file_ids": [fid], "storage_chat": "@cleanup-chan"},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["enqueued"]) == 1, r.json()

    for _ in range(120):
        row = await db.fetch_one(
            "SELECT status FROM jobs WHERE kind='transfer' ORDER BY seq DESC LIMIT 1"
        )
        if row and row["status"] in ("done", "failed"):
            break
        import asyncio

        await asyncio.sleep(0.05)
    assert row["status"] == "done", row

    # source copy still there (setting is off by default)
    assert (chat, mid) in FakeBackend.STORE
