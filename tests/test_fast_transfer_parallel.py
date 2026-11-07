"""Parallel MTProto upload: parts are independent requests, not one stream."""
from __future__ import annotations

import os

import pytest

from app.tg import fast_transfer
from telethon.tl import types


class FakeUploadClient:
    def __init__(self) -> None:
        self.requests = []
        self.small_uploads = 0

    async def __call__(self, request):
        self.requests.append(request)
        return True

    async def upload_file(self, *args, **kwargs):
        self.small_uploads += 1
        return object()


@pytest.mark.asyncio
async def test_upload_file_issues_parallel_big_parts(tmp_path):
    path = tmp_path / "eleven-megabytes.bin"
    path.write_bytes(os.urandom(11 * 1024 * 1024))
    client = FakeUploadClient()
    seen = []

    async def progress(done: int, total: int) -> None:
        seen.append((done, total))

    handle = await fast_transfer.upload_file(
        client, str(path), file_name="eleven-megabytes.bin",
        part_workers=3, progress=progress,
    )

    assert isinstance(handle, types.InputFileBig)
    assert client.small_uploads == 0
    assert len(client.requests) == handle.parts
    assert {r.file_part for r in client.requests} == set(range(handle.parts))
    assert handle.parts > 1
    assert max(done for done, _total in seen) == os.path.getsize(path)
