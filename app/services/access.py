"""Per-account chat access with a small TTL cache.

The old pool picked any free account for a download.  For a file stored in a
private channel that account may not be a member, so a ready file could fail
(or worse, a different account's ``me`` could be read).  This service records
what a successful upload already proved and lazily probes unknown accounts.

The cache is deliberately advisory: stale positives are retried against
another account at the caller, and unknown backends (bots/eitaa) keep the old
pool behaviour.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional

from ..core.config import get_settings
from ..core.db import Database
from ..core.metrics import metrics
from ..core.settings_service import get_runtime
from ..tg.base import FloodWait, SendFailure, TransferError
from ..tg.manager import Borrowed, NoBackendAvailable, TGManager


def normalize_chat(chat: str) -> str:
    """Canonical cache key: lowercase @username, numeric id, or ``me``."""
    value = (chat or "").strip()
    if not value:
        return "me"
    if value.lower() in ("me", "self"):
        return "me"
    if value.startswith("@"):
        return value.lower()
    if value.lstrip("-").isdigit():
        try:
            return str(int(value))
        except ValueError:
            return value
    return value.lower()


class AccessService:
    def __init__(self, db: Database, manager: TGManager) -> None:
        self.db = db
        self.manager = manager
        self._settings = get_settings()

    async def _cfg_int(self, name: str, default: int) -> int:
        try:
            value = await get_runtime(self.db, name)
            if value not in (None, ""):
                return int(value)
        except Exception:
            pass
        return int(getattr(self._settings, name, default) or default)

    async def get(self, account_key: str, chat: str, backend: str = "telegram") -> Optional[dict]:
        if not account_key or not chat:
            return None
        row = await self.db.fetch_one(
            "SELECT backend, account_key, chat_key, can_read, can_write, checked_at,"
            " expires_at, last_error, source FROM chat_access"
            " WHERE backend=? AND account_key=? AND chat_key=?",
            (backend or "telegram", account_key, normalize_chat(chat)),
        )
        if not row:
            metrics.inc("access.cache_miss")
            return None
        if float(row.get("expires_at") or 0) <= time.time():
            metrics.inc("access.cache_miss")
            return None
        metrics.inc("access.cache_hit")
        return row

    async def mark(
        self,
        account_key: str,
        chat: str,
        backend: str = "telegram",
        *,
        can_read: Optional[bool] = None,
        can_write: Optional[bool] = None,
        source: str = "",
        error: str = "",
        ttl: Optional[float] = None,
    ) -> None:
        if not account_key or not chat:
            return
        backend = backend or "telegram"
        chat_key = normalize_chat(chat)
        old = await self.get(account_key, chat, backend)
        read = int(bool(can_read)) if can_read is not None else int(bool(old.get("can_read")) if old else 0)
        write = int(bool(can_write)) if can_write is not None else int(bool(old.get("can_write")) if old else 0)
        positive = read or write
        if ttl is None:
            ttl = await self._cfg_int(
                "access_cache_ttl_s" if positive else "access_cache_negative_ttl_s",
                self._settings.access_cache_ttl_s if positive else self._settings.access_cache_negative_ttl_s,
            )
        now = time.time()
        vals = (
            read, write, now, now + max(1, int(ttl)),
            (error or "")[:500], (source or "")[:32],
            backend, account_key, chat_key,
        )
        updated = await self.db.execute(
            "UPDATE chat_access SET can_read=?, can_write=?, checked_at=?, expires_at=?,"
            " last_error=?, source=? WHERE backend=? AND account_key=? AND chat_key=?",
            vals,
        )
        if updated:
            return
        try:
            await self.db.execute(
                "INSERT INTO chat_access(backend, account_key, chat_key, can_read, can_write,"
                " checked_at, expires_at, last_error, source) VALUES(?,?,?,?,?,?,?,?,?)",
                (backend, account_key, chat_key, read, write, now, now + max(1, int(ttl)),
                 (error or "")[:500], (source or "")[:32]),
            )
        except Exception:
            # a concurrent probe inserted the same primary key first
            await self.db.execute(
                "UPDATE chat_access SET can_read=?, can_write=?, checked_at=?, expires_at=?,"
                " last_error=?, source=? WHERE backend=? AND account_key=? AND chat_key=?",
                vals,
            )

    async def invalidate(self, account_key: str, chat: str, backend: str = "telegram") -> None:
        await self.db.execute(
            "DELETE FROM chat_access WHERE backend=? AND account_key=? AND chat_key=?",
            (backend or "telegram", account_key, normalize_chat(chat)),
        )

    async def probe(
        self, account_key: str, chat: str, backend: str = "telegram", need: str = "read"
    ) -> Optional[bool]:
        """Ask one account whether it can read/write the chat.

        ``None`` means "unknown/transient" (bot, eitaa, network error); callers
        then retain the legacy pooled fallback.
        """
        try:
            borrowed = await self.manager.acquire_key(
                account_key, timeout=5.0, direction="control"
            )
        except NoBackendAvailable:
            return None
        try:
            async with borrowed as be:
                result = await asyncio.wait_for(
                    be.probe_access(chat, need), timeout=15.0
                )
            if result is None:
                metrics.inc("access.probe_unknown")
                return None
            if need == "write":
                await self.mark(
                    account_key, chat, backend, can_write=bool(result),
                    source="probe", error="" if result else "write denied",
                )
            else:
                await self.mark(
                    account_key, chat, backend, can_read=bool(result),
                    source="probe", error="" if result else "read denied",
                )
            metrics.inc("access.probe_ok" if result else "access.probe_denied")
            return bool(result)
        except FloodWait:
            metrics.inc("access.probe_unknown")
            return None
        except TransferError:
            metrics.inc("access.probe_unknown")
            return None
        except SendFailure as exc:
            await self.mark(
                account_key, chat, backend,
                can_write=False if need == "write" else None,
                can_read=False if need == "read" else None,
                source="probe", error=str(exc),
            )
            metrics.inc("access.probe_denied")
            return False
        except Exception:
            metrics.inc("access.probe_unknown")
            return None

    async def pick(
        self,
        chat: str,
        *,
        backend: str = "telegram",
        need: str = "read",
        preferred: str = "",
        exclude: tuple[str, ...] = (),
        timeout: float = 30.0,
    ) -> Borrowed:
        """Borrow an account that is known/probed to have access to ``chat``.

        Known positives are tried first (preferred uploader first).  Unknown
        accounts are probed lazily; an unprobeable backend falls back to the
        normal pool so fake/eitaa/bot paths keep working.
        """
        if not await self._cfg_int("access_cache_enabled", 1):
            metrics.inc("access.cache_disabled")
            return await self.manager.acquire(
                "acc", backend=backend, direction="download" if need == "read" else "upload"
            )

        keys: List[str] = []
        if preferred and preferred not in exclude:
            keys.append(preferred)
        keys.extend(
            k for k in self.manager.account_keys()
            if k not in exclude and k not in keys
        )
        if not keys:
            metrics.inc("access.no_accounts")
            return await self.manager.acquire(
                "acc", backend=backend, direction="download" if need == "read" else "upload"
            )

        flag = "can_read" if need == "read" else "can_write"
        direction = "download" if need == "read" else "upload"
        unknown: List[str] = []
        for key in keys:
            row = await self.get(key, chat, backend)
            if row is None:
                unknown.append(key)
                continue
            if not int(row.get(flag) or 0):
                metrics.inc("access.denied_skipped")
                continue
            try:
                if key == preferred:
                    metrics.inc("access.preferred_used")
                return await self.manager.acquire_key(
                    key, timeout=min(10.0, timeout), direction=direction
                )
            except NoBackendAvailable:
                continue

        limit = max(1, await self._cfg_int("access_probe_limit", 3))
        unresolved: List[str] = []
        for key in unknown[:limit]:
            result = await self.probe(key, chat, backend, need)
            if result:
                try:
                    return await self.manager.acquire_key(
                        key, timeout=min(10.0, timeout), direction=direction
                    )
                except NoBackendAvailable:
                    continue
            elif result is None:
                unresolved.append(key)
        unresolved.extend(unknown[limit:])

        # Every candidate was unknown (fake/eitaa/bot or an unsupported probe):
        # preserve the old pooled selection so existing deployments and the
        # auto-split round-robin keep working.
        if unresolved and len(unresolved) == len(keys):
            return await self.manager.acquire(
                "acc", backend=backend, direction=direction
            )

        # Some accounts are known-denied; only try the genuinely unknown ones.
        for key in unresolved:
            try:
                return await self.manager.acquire_key(
                    key, timeout=min(5.0, timeout), direction=direction
                )
            except NoBackendAvailable:
                continue
        # Uploads are safe to fail-open: the actual Telegram send is the final
        # authority, and a cached false negative (or a session that cannot
        # resolve the channel entity yet) must not block an otherwise valid
        # upload forever.  Downloads stay strict: reading another account's
        # Saved Messages would leak the wrong file.
        if need == "write":
            metrics.inc("access.write_fallback")
            return await self.manager.acquire(
                "acc", backend=backend, direction="upload"
            )
        metrics.inc("access.unavailable")
        raise NoBackendAvailable(
            f"no account can access storage chat {chat!r}", 30.0, retryable=False
        )
