"""Telegram proxy pool service.

- parse_share_link: tg://proxy?... & https://t.me/proxy?... & raw host:port[:user:pass]
- speed_test / speed_test_all: TCP connect + optional Telegram DC handshake latency
- get_active_proxy: strategy-aware selection (speed order / round-robin)
- build_telethon_proxy: Telethon proxy arg builder (python-socks / MTProto)
- Global enable/disable via runtime settings (proxy_enabled).
"""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..core.config import get_settings
from ..core.models import ProxyRepo, now

# Production Telegram DC IPs (DC 1..5) — used for the handshake latency probe
TG_DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
DEFAULT_DC = 2  # DC4/2 hosts most media traffic; good default target

CHECK_TIMEOUT = 8.0  # seconds per probe


# ── share-link parsing ───────────────────────────────────────────
def parse_share_link(text: str) -> Dict[str, Any]:
    """Parse tg://proxy?server=..&port=..&secret=.. | t.me/proxy | socks5://user:pass@host:port | host:port[:user:pass]."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("ورودی خالی است")

    low = raw.lower()
    if low.startswith("tg://proxy") or "t.me/proxy" in low or "telegram.me/proxy" in low:
        parsed = urlparse(raw if "://" in raw else "tg://" + raw)
        qs = parse_qs(parsed.query)
        server = (qs.get("server") or [""])[0]
        port = (qs.get("port") or [""])[0]
        secret = (qs.get("secret") or [""])[0]
        if not server or not port.isdigit():
            raise ValueError("لینک پراکسی نامعتبر است (server/port)")
        kind = "mtproto"
        if len(secret) == 32:
            # hex secret may carry "prefs" prefix (dd…) for tagged proxies
            kind = "mtproto"
        elif secret.startswith("ee") and len(secret) >= 32:
            kind = "mtproto"  # secure/randomized
        return {"kind": "mtproto", "host": server, "port": int(port), "secret_hex": secret.lower(), "username": "", "password": ""}

    if "://" in raw:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "socks5").lower()
        if scheme not in ("socks5", "socks4", "http", "https"):
            raise ValueError(f"پروتکل پشتیبانی نمی‌شود: {scheme}")
        kind = "http" if scheme in ("http", "https") else "socks5"
        username = parsed.username or ""
        password = parsed.password or ""
        host = parsed.hostname or ""
        port = parsed.port or (8080 if kind == "http" else 1080)
        if not host:
            raise ValueError("هاست پراکسی نامعتبر است")
        return {"kind": kind, "host": host, "port": int(port), "secret_hex": "", "username": username, "password": password}

    # bare host:port[:user:pass]
    parts = raw.split(":")
    if len(parts) == 2 and parts[1].isdigit():
        return {"kind": "socks5", "host": parts[0], "port": int(parts[1]), "secret_hex": "", "username": "", "password": ""}
    if len(parts) == 4 and parts[1].isdigit():
        return {"kind": "socks5", "host": parts[0], "port": int(parts[1]), "secret_hex": "", "username": parts[2], "password": parts[3]}
    raise ValueError("فرمت پشتیبانی نمی‌شود: tg://proxy یا t.me/proxy یا socks5:// یا host:port")


# ── telethon proxy arg ───────────────────────────────────────────
def build_telethon_proxy(row: Dict[str, Any]) -> Optional[tuple]:
    """Build the Telethon ``proxy`` argument for a proxies-table row.

    - mtproto → (ConnectionTcpMTProxyRandomizedIntermediate, host, port, secret_bytes)
    - socks5/http → python-socks 6-tuple (python-socks[asyncio] required)
    Returns None when dependencies/params are missing (caller should skip proxying).
    """
    kind = (row.get("kind") or "").lower()
    host = row.get("host") or ""
    port = int(row.get("port") or 0)
    if not host or not port:
        return None
    try:
        if kind == "mtproto":
            from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate

            secret = (row.get("secret_hex") or "").replace(" ", "")
            if secret.startswith("ee"):
                secret = secret[2:]
            try:
                secret_bytes = bytes.fromhex(secret)
            except ValueError:
                return None
            return (ConnectionTcpMTProxyRandomizedIntermediate, host, port, secret_bytes)

        import python_socks  # noqa: F401  (presence check)

        proxy_type = 2 if kind == "http" else 1  # python_socks.ProxyType: 1=SOCKS5, 2=HTTP
        rdns = True
        return (
            proxy_type,
            host,
            int(port),
            bool(rdns),
            row.get("username") or None,
            row.get("_password_plain") or None,
        )
    except ImportError:
        return None


def proxy_signature(row: Dict[str, Any]) -> str:
    return f"{row.get('kind')}://{row.get('host')}:{row.get('port')}"


# ── speed testing ────────────────────────────────────────────────
async def _tcp_connect_latency(host: str, port: int, timeout: float) -> float:
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    fut = loop.run_in_executor(None, lambda: socket.create_connection((host, port), timeout=timeout).close())
    await asyncio.wait_for(fut, timeout=timeout + 2)
    return (time.perf_counter() - t0) * 1000


async def speed_test_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Probe one proxy: TCP connect latency (works for MTProto/SOCKS5/HTTP alike).

    The real end-to-end check happens implicitly when accounts connect through
    the pool (TGManager passes the chosen proxy to Telethon).
    """
    host, port = row["host"], int(row["port"])
    started = now()
    try:
        latency = await _tcp_connect_latency(host, port, CHECK_TIMEOUT)
        row.update(status="ok", latency_ms=round(latency, 1), last_error="", last_checked_at=started)
        return row
    except Exception as exc:
        row.update(status="down", latency_ms=-1, last_error=str(exc)[:300], last_checked_at=started)
        return row


async def speed_test_all(repo: ProxyRepo, proxy_id: Optional[int] = None, concurrent: int = 10) -> List[Dict[str, Any]]:
    """Test all (or one) proxies concurrently, persist results, return rows sorted by speed."""
    rows = await repo.list()
    if proxy_id is not None:
        rows = [r for r in rows if r["id"] == proxy_id]
    if not rows:
        return []

    sem = asyncio.Semaphore(max(1, concurrent))

    async def _one(row: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            return await speed_test_row(row)

    results = await asyncio.gather(*(_one(r) for r in rows))
    for row in results:
        await repo.set_check_result(row["id"], row["status"], row["latency_ms"], row.get("last_error", ""))
    return await repo.list()


# ── selection for the transfer pool ──────────────────────────────
class ProxySelector:
    """Chooses the proxy used for new backend connections (per process).

    Tracks the currently-active proxy; when callers report it dead
    (report_dead), the next get_active_proxy skips past it and picks the
    next-fastest healthy proxy (fallback), until the pool is re-tested
    (invalidate) or a monitor pass restores trust.
    """

    DEAD_TTL = 300.0  # seconds a reported-dead proxy stays skipped

    def __init__(self) -> None:
        self._rr = 0
        self._cache: List[Dict[str, Any]] = []
        self._cache_at: float = 0.0
        self._dead: Dict[int, float] = {}  # proxy_id -> reported-dead timestamp

    def invalidate(self) -> None:
        self._cache_at = 0.0

    def report_dead(self, proxy_id: int) -> None:
        """Mark the active proxy dead (connection refused/timeout through it).
        Next selection falls back to the next-best proxy."""
        self._dead[proxy_id] = time.time()
        self._cache_at = 0.0  # force re-selection

    def report_healthy(self, proxy_id: int) -> None:
        self._dead.pop(proxy_id, None)

    def _dead_now(self) -> set:
        now_ts = time.time()
        return {pid for pid, ts in self._dead.items() if now_ts - ts < self.DEAD_TTL}

    async def get_active_proxy(self, db, dc_id: int = DEFAULT_DC) -> Optional[Dict[str, Any]]:
        """Return the row to use, honoring runtime settings proxy_enabled/proxy_strategy."""
        from ..core.settings_service import get_runtime

        s = get_settings()
        try:
            enabled = bool(int(await get_runtime(db, "proxy_enabled") or 0))
        except Exception:
            enabled = False
        if not enabled:
            return None

        dead = self._dead_now()
        if time.time() - self._cache_at > 30.0 or (self._cache and self._cache[0]["id"] in dead):
            repo = ProxyRepo(db)
            try:
                strategy = str(await get_runtime(db, "proxy_strategy") or "speed")
            except Exception:
                strategy = "speed"
            rows = await repo.list_enabled_sorted()
            usable = [r for r in rows if r.get("status") in ("ok", "degraded", "unknown")]
            if strategy == "rr":
                if usable:
                    self._rr += 1
                    ordered = usable[self._rr % len(usable):] + usable[: self._rr % len(usable)]
                    usable = ordered
            elif strategy == "speed":
                usable = [r for r in usable if r.get("status") in ("ok", "degraded")] or (
                    [r for r in rows if r.get("status") == "unknown"]
                )
            # fallback: skip proxies reported dead recently
            usable = [r for r in usable if r["id"] not in dead]
            # cache only non-empty results: adding the first proxy must take
            # effect immediately (mutations call selector.invalidate() too)
            if usable:
                self._cache = usable[:1]
                self._cache_at = time.time()
            else:
                # everything dead/unavailable → allow direct (None) but keep
                # dead marks so a later healthy re-test restores them
                self._cache = []
        return self._cache[0] if self._cache else None


selector = ProxySelector()


async def get_active_proxy(db, dc_id: int = DEFAULT_DC) -> Optional[Dict[str, Any]]:
    return await selector.get_active_proxy(db, dc_id)


async def retest_proxy(db, proxy_id: int) -> Optional[Dict[str, Any]]:
    """Re-probe one proxy (used after a connection through it failed)."""
    repo = ProxyRepo(db)
    rows = await speed_test_all(repo, proxy_id=proxy_id)
    return rows[0] if rows else None
