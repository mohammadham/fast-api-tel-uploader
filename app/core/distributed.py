"""Optional Redis-backed coordination shared by every queue node.

The database stays authoritative: this module only provides a fast shared
rate budget and a wake-up bus. Every helper degrades to ``None`` when Redis is
disabled or unreachable, so callers keep their in-process fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Tuple

import redis.asyncio as redis

log = logging.getLogger("tgdrive.distributed")

#: cached client shared by queue rate budgets (URL-keyed; one per process)
_client: Optional[redis.Redis] = None
_client_url = ""
_failed_url = ""
_failed_at = 0.0
_lock = asyncio.Lock()

_FAILURE_COOLDOWN = 30.0


def redact_redis_url(url: str) -> str:
    """Hide the password in a Redis URL before it reaches logs.

    ``redis://user:secret@host:6379/0`` becomes ``redis://user:***@host:6379/0``.
    URLs without credentials are returned unchanged.
    """
    raw = str(url or "")
    if "://" not in raw or "@" not in raw:
        return raw
    scheme, rest = raw.split("://", 1)
    if scheme == "unix":
        return raw
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


async def _settings(db) -> Tuple[bool, str]:
    """(enabled, url), with runtime DB settings winning over env defaults."""
    from .config import get_settings
    from .settings_service import runtime_settings

    s = get_settings()
    enabled = bool(s.redis_enabled)
    url = (s.redis_url or "").strip()
    try:
        vals = await runtime_settings().get_all(db, force=False)
        if "redis_enabled" in vals:
            enabled = bool(int(vals.get("redis_enabled") or 0))
        if str(vals.get("redis_url") or "").strip():
            url = str(vals.get("redis_url")).strip()
    except Exception:
        pass
    return enabled, url


async def _close_current() -> None:
    global _client, _client_url
    old, _client, _client_url = _client, None, ""
    if old is not None:
        try:
            await old.aclose()
        except Exception:
            pass


async def get_client(db) -> Optional[redis.Redis]:
    """Return a live Redis client, or ``None`` when coordination is off/down.

    A failed URL is not retried for 30 seconds, so a dead Redis never adds a
    connect timeout to every queue claim.
    """
    global _client, _client_url, _failed_url, _failed_at

    enabled, url = await _settings(db)
    async with _lock:
        if not enabled or not url:
            if _client is not None:
                await _close_current()
            return None
        if _client is not None and _client_url == url:
            return _client
        if _failed_url == url and (time.time() - _failed_at) < _FAILURE_COOLDOWN:
            return None
        candidate = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            await candidate.ping()
        except Exception as exc:
            try:
                await candidate.aclose()
            except Exception:
                pass
            _failed_url, _failed_at = url, time.time()
            log.warning(
                "distributed Redis unavailable at %s (%s); local fallback",
                redact_redis_url(url),
                type(exc).__name__,
            )
            return None
        await _close_current()
        _client, _client_url = candidate, url
        _failed_url, _failed_at = "", 0.0
        log.info("distributed coordination enabled at %s", redact_redis_url(url))
        return _client


async def allow(db, key: str, capacity: int, per_minute: int) -> Optional[bool]:
    """Consume one unit from a shared minute bucket.

    Returns ``True``/``False`` when Redis is available, or ``None`` when the
    caller must fall back to its in-process limiter. The fixed minute window is
    deliberately simple: correctness (a fleet-wide cap) matters more than
    perfectly smooth refill, which the local token bucket still provides when
    Redis is off.
    """
    if capacity <= 0 or per_minute <= 0:
        return None
    client = await get_client(db)
    if client is None:
        return None
    minute = int(time.time() // 60)
    bucket = f"tgdrive:rl:{key}:{minute}"
    try:
        used = int(await client.incr(bucket))
        if used == 1:
            await client.expire(bucket, 120)
        return used <= int(capacity)
    except Exception as exc:
        log.warning("Redis rate budget failed (%s); local fallback", type(exc).__name__)
        return None
