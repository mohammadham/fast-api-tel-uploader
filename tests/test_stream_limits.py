"""Per-key stream caps and their release path."""
from __future__ import annotations

from app.services.stream_limits import StreamLimiter
from app.services.streaming import _ReleaseAndCount, _stream_policy, streams


async def test_stream_limiter_caps_and_releases():
    lim = StreamLimiter()
    assert await lim.acquire("key:1", 2) is True
    assert await lim.acquire("key:1", 2) is True
    assert await lim.acquire("key:1", 2) is False
    assert lim.inflight("key:1") == 2

    lim.release("key:1")
    assert await lim.acquire("key:1", 2) is True
    assert lim.stats()["key:1"] == 2


async def test_stream_limiter_isolates_subjects():
    lim = StreamLimiter()
    assert await lim.acquire("a", 1) is True
    assert await lim.acquire("b", 1) is True
    assert await lim.acquire("a", 1) is False


async def test_stream_policy_uses_runtime_values(monkeypatch):
    import app.core.settings_service as settings_service

    async def runtime(_db, key):
        return {"stream_max_per_key": 7, "stream_retry_after_s": 11}.get(key)

    monkeypatch.setattr(settings_service, "get_runtime", runtime)
    assert await _stream_policy(None) == (7, 11)


class _DummyManager:
    def __init__(self) -> None:
        self.released = False

    async def release(self, key, exc):
        self.released = True

    async def note_bytes(self, key, n):
        pass


async def test_release_background_frees_stream_slot():
    key = "test:release-background"
    assert await streams.acquire(key, 1) is True
    bg = _ReleaseAndCount(_DummyManager(), "bot:1", None, "f1", stream_key=key)
    await bg()
    assert await streams.acquire(key, 1) is True
    streams.release(key)
