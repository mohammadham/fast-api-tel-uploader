"""Distributed Redis coordination: redaction, shared rate budget, leases."""
from __future__ import annotations

import pytest

from app.core import distributed
from app.queue.queue_manager import QueueManager
from app.tg.base import FloodWait
from app.tg.manager import TGManager


class FakeRateRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


class FakeLeaseRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, px=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, script, numkeys, key, *args):
        if self.data.get(key) != args[0]:
            return 0
        if "DEL" in script:
            self.data.pop(key, None)
        return 1


def test_redact_redis_url_hides_password_only():
    assert (
        distributed.redact_redis_url("redis://user:s3cret@cache:6379/0")
        == "redis://user:***@cache:6379/0"
    )
    assert distributed.redact_redis_url("redis://cache:6379/0") == "redis://cache:6379/0"
    assert distributed.redact_redis_url("") == ""


async def test_shared_rate_budget_enforces_fleet_cap(monkeypatch):
    fake = FakeRateRedis()

    async def client(_db):
        return fake

    monkeypatch.setattr(distributed, "get_client", client)
    assert await distributed.allow(None, "tg:acc:1:download", 2, 2) is True
    assert await distributed.allow(None, "tg:acc:1:download", 2, 2) is True
    assert await distributed.allow(None, "tg:acc:1:download", 2, 2) is False
    assert fake.expires, "first hit must set a TTL on the minute bucket"


async def test_shared_rate_budget_falls_back_when_redis_off(monkeypatch):
    async def client(_db):
        return None

    monkeypatch.setattr(distributed, "get_client", client)
    assert await distributed.allow(None, "bot:1", 10, 10) is None


async def test_redis_lease_is_exclusive_and_released_by_owner():
    qm = QueueManager(None, None, node_id="test-node")
    qm._redis = FakeLeaseRedis()

    assert await qm._redis_claim_lease("j1", "owner-a", 60) is True
    assert await qm._redis_claim_lease("j1", "owner-b", 60) is False
    assert await qm._redis_renew_lease("j1", "owner-a", 60) is True
    assert await qm._redis_renew_lease("j1", "owner-b", 60) is False

    await qm._redis_release_lease("j1", "owner-b")  # wrong owner: no-op
    assert await qm._redis_claim_lease("j1", "owner-b", 60) is False
    await qm._redis_release_lease("j1", "owner-a")
    assert await qm._redis_claim_lease("j1", "owner-b", 60) is True


async def test_lease_helpers_degrade_without_redis():
    qm = QueueManager(None, None, node_id="test-node")
    assert await qm._redis_claim_lease("j1", "owner", 60) is True
    assert await qm._redis_renew_lease("j1", "owner", 60) is False


async def test_floodwait_halves_and_success_recovers_adaptive_cap():
    mgr = TGManager(None)
    key = "bot:42"
    assert mgr._effective_cap(key, "download") == 2

    await mgr.release(key, FloodWait(3), "download")
    assert mgr._effective_cap(key, "download") == 1

    await mgr.release(key, None, "download")
    assert mgr._effective_cap(key, "download") == 2
