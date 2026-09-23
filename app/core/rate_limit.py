"""Token-bucket rate limiting (per API key / per IP) and daily quota accounting."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_sec: float
    tokens: float = field(init=False)
    updated: float = field(init=False, default_factory=time.monotonic)
    last_seen: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def try_take(self, amount: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


class RateLimiter:
    """In-memory token buckets keyed by arbitrary string (api key id / ip).

    Buckets are lazy-created per subject; idle buckets are garbage-collected
    periodically so per-IP tracking behind a public endpoint cannot grow
    without bound.
    """

    GC_INTERVAL = 600.0      # seconds between sweeps
    IDLE_AFTER = 1800.0      # remove buckets idle longer than this

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._last_gc = time.monotonic()

    async def allow(self, key: str, capacity: float, per_minute: float) -> bool:
        async with self._lock:
            now = time.monotonic()
            if now - self._last_gc >= self.GC_INTERVAL:
                self._gc(now)
                self._last_gc = now
            refill = per_minute / 60.0
            b = self._buckets.get(key)
            if b is None or b.capacity != capacity or b.refill_per_sec != refill:
                # new subject or reconfigured limits → fresh bucket
                b = TokenBucket(capacity=capacity, refill_per_sec=refill)
                self._buckets[key] = b
            b.last_seen = now
            return b.try_take()

    def _gc(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.last_seen > self.IDLE_AFTER]
        for k in stale:
            del self._buckets[k]

    def stats(self) -> dict[str, int]:
        return {"tracked_subjects": len(self._buckets)}


limiter = RateLimiter()


class QuotaExceeded(Exception):
    pass


class DailyQuota:
    """Tracks bytes transferred per subject per UTC day (in-memory + DB persists counters)."""

    def __init__(self) -> None:
        self._bytes: dict[tuple[str, str], float] = {}
        self._day: dict[str, str] = {}

    @staticmethod
    def today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def add(self, subject: str, nbytes: int) -> None:
        day = self.today()
        self._day[subject] = day
        self._bytes[(subject, day)] = self._bytes.get((subject, day), 0) + nbytes

    def used(self, subject: str) -> float:
        return self._bytes.get((subject, self.today()), 0.0)

    def reset_if_new_day(self, subject: str) -> None:
        if self._day.get(subject) != self.today():
            self._bytes[(subject, self.today())] = 0.0
            self._day[subject] = self.today()


quota = DailyQuota()
