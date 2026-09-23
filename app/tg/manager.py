"""Backend pool manager: borrow/release with concurrency caps, flood isolation,
circuit breaker and health stats. Backend-agnostic (works with fake or real)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from ..core.config import get_settings
from ..core.db import Database
from ..core.models import AccountRepo, BotRepo, EitaaAccountRepo
from ..core.metrics import metrics
from .base import BackendClient, FloodWait, SendFailure, TransferError

log = logging.getLogger("tgdrive.manager")

CIRCUIT_THRESHOLD = 5
CIRCUIT_COOLDOWN = 300  # seconds

# sessions telegram has permanently invalidated → pool must drop them
_PERMANENT_ERROR_NAMES = {
    "AuthKeyUnregisteredError",
    "AuthKeyDuplicatedError",
    "AuthKeyInvalidError",
    "UserDeactivatedBanError",
}


class NoBackendAvailable(Exception):
    pass


class Borrowed:
    def __init__(self, manager: "TGManager", backend: BackendClient, key: str) -> None:
        self._manager = manager
        self.backend = backend
        self.key = key

    async def __aenter__(self) -> BackendClient:
        return self.backend

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._manager.release(self.key, exc)


class TGManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._backends: Dict[str, BackendClient] = {}
        self._sems: Dict[str, asyncio.Semaphore] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._flood_until: Dict[str, float] = {}
        self._circuit_until: Dict[str, float] = {}
        self._errors: Dict[str, int] = {}
        self._flood_counts: Dict[str, int] = {}
        self._rr: int = 0
        self._stopped = False

    def _settings(self):
        return get_settings()

    async def start(self) -> None:
        self._stopped = False
        await self._spawn_all()

    async def stop(self) -> None:
        self._stopped = True
        for b in list(self._backends.values()):
            try:
                await b.close()
            except Exception:
                pass
        self._backends.clear()

    async def _spawn_all(self) -> None:
        s = self._settings()
        accounts = AccountRepo(self.db)
        bots = BotRepo(self.db)
        eitaas = EitaaAccountRepo(self.db)
        for row in await accounts.list():
            try:
                await self._ensure_account(row["id"])
            except Exception as exc:
                log.warning("account %s failed to load: %s", row["id"], exc)
        for row in await bots.list():
            try:
                await self._ensure_bot(row["id"])
            except Exception as exc:
                log.warning("bot %s failed to load: %s", row["id"], exc)
        for row in await eitaas.list():
            try:
                await self._ensure_eitaa(row["id"])
            except Exception as exc:
                log.warning("eitaa %s failed to load: %s", row["id"], exc)

    def _sem(self, key: str) -> asyncio.Semaphore:
        s = self._settings()
        if key.startswith("acc:"):
            cap = max(1, s.max_concurrent_downloads // max(1, len(self._backends) or 1))
        else:
            cap = 2
        if key not in self._sems:
            self._sems[key] = asyncio.Semaphore(cap)
        return self._sems[key]

    def _available(self, key: str) -> bool:
        now = time.time()
        return (
            self._flood_until.get(key, 0) < now
            and self._circuit_until.get(key, 0) < now
        )

    async def refresh_one_account(self, account_id: int) -> None:
        await self._ensure_account(account_id)

    async def refresh_one_bot(self, bot_id: int) -> None:
        await self._ensure_bot(bot_id)

    async def refresh_one_eitaa(self, eitaa_id: int) -> None:
        await self._ensure_eitaa(eitaa_id)

    async def drop(self, key_prefix: str, record_id: int) -> None:
        key = f"{key_prefix}{record_id}"
        b = self._backends.pop(key, None)
        if b:
            try:
                await b.close()
            except Exception:
                pass
        self._sems.pop(key, None)
        self._errors.pop(key, None)
        self._flood_until.pop(key, None)
        self._circuit_until.pop(key, None)

    async def _ensure_account(self, account_id: int) -> None:
        repo = AccountRepo(self.db)
        row = await repo.get(account_id)
        if not row or not row["session_enc"] or not row["enabled"]:
            self._backends.pop(f"acc:{account_id}", None)
            return
        key = f"acc:{account_id}"
        if key in self._backends:
            return
        from ..core.security import decrypt_str
        from .telethon_backend import TelethonBackend

        s = self._settings()
        if s.fake_tg:
            from .fake import FakeBackend

            backend = FakeBackend(cid=key)
        else:
            session = decrypt_str(row["session_enc"])
            backend = TelethonBackend(
                key,
                session,
                api_id=s.tg_api_id,
                api_hash=s.tg_api_hash,
                storage_chat=row["storage_chat_id"] or "me",
            )
            await backend.start()
        self._backends[key] = backend

    async def _ensure_bot(self, bot_id: int) -> None:
        repo = BotRepo(self.db)
        row = await repo.get(bot_id)
        if not row or not row["enabled"]:
            self._backends.pop(f"bot:{bot_id}", None)
            return
        key = f"bot:{bot_id}"
        if key in self._backends:
            return
        from ..core.security import decrypt_str
        from .telethon_backend import BotBackend

        s = self._settings()
        if s.fake_tg:
            from .fake import FakeBackend

            backend = FakeBackend(cid=key)
        else:
            token = decrypt_str(row["token_enc"])
            backend = BotBackend(key, token, base=s.bot_api_base)
        self._backends[key] = backend

    async def _ensure_eitaa(self, eitaa_id: int) -> None:
        repo = EitaaAccountRepo(self.db)
        row = await repo.get(eitaa_id)
        if not row or not row["enabled"]:
            self._backends.pop(f"eit:{eitaa_id}", None)
            return
        key = f"eit:{eitaa_id}"
        if key in self._backends:
            return
        from ..core.security import decrypt_str

        if self._settings().fake_tg:
            from .fake import FakeBackend

            backend = FakeBackend(cid=key)
        else:
            from .eitaa_backend import EitaaBackend

            backend = EitaaBackend(key, decrypt_str(row["token_enc"]), row["chat_id"])
        backend.storage_chat = row["chat_id"]  # destination chat for both real+fake
        self._backends[key] = backend

    def _candidates(self, kind_hint: str, backend: str = "") -> List[str]:
        s = self._settings()
        out: List[str] = []
        acc_keys = sorted(k for k in self._backends if k.startswith("acc:"))
        bot_keys = sorted(k for k in self._backends if k.startswith("bot:"))
        eit_keys = sorted(k for k in self._backends if k.startswith("eit:"))
        if backend == "eitaa":
            return eit_keys
        if kind_hint == "bot" and bot_keys:
            return bot_keys
        if acc_keys:
            out.extend(acc_keys)
        if s.fake_tg:
            out.extend(bot_keys)
        return out

    async def acquire(self, kind_hint: str = "acc", timeout: float = 30.0, backend: str = "") -> Borrowed:
        """Borrow a healthy backend with free slot; waits if all busy/flooded.

        Selection is round-robin across eligible backends so uploads spread
        evenly over the account pool instead of always loading the first one.
        ``backend`` pins the pool: "eitaa" → eitaayar backends only; ""/"telegram"
        → account pool as before.
        Raises NoBackendAvailable when nothing is configured or the wait
        deadline passes, so queue jobs fail loudly instead of hanging.
        """
        deadline = time.time() + max(0.0, timeout)
        while not self._stopped:
            keys = [k for k in self._candidates(kind_hint, backend) if self._available(k)]
            if keys:
                self._rr += 1
                ordered = keys[self._rr % len(keys) :] + keys[: self._rr % len(keys)]
                for key in ordered:
                    sem = self._sem(key)
                    if sem._value > 0:  # free slot?
                        await sem.acquire()
                        if not self._available(key):
                            sem.release()
                            continue
                        return Borrowed(self, self._backends[key], key)
            if not self._candidates(kind_hint, backend) and not self._stopped:
                # nothing configured at all → fail fast (caller will retry/fail)
                raise NoBackendAvailable("no telegram backend configured")
            if time.time() >= deadline:
                raise NoBackendAvailable("no available backend within timeout")
            await asyncio.sleep(0.1)

    async def acquire_key(self, key: str, timeout: float = 30.0) -> Borrowed:
        """Borrow one specific backend (health probes, targeted ops)."""
        if key not in self._backends:
            raise NoBackendAvailable(f"backend {key} not loaded")
        deadline = time.time() + max(0.0, timeout)
        while not self._stopped:
            sem = self._sem(key)
            if sem._value > 0:
                await sem.acquire()
                return Borrowed(self, self._backends[key], key)
            if time.time() >= deadline:
                raise NoBackendAvailable(f"backend {key} busy")
            await asyncio.sleep(0.1)
        raise NoBackendAvailable("manager stopped")

    def release_stats(self, key: str) -> None:
        """Reset transient error counter after a successful direct probe."""
        self._errors.pop(key, None)
        self._flood_counts.pop(key, None)

    async def release(self, key: str, exc: Optional[BaseException]) -> None:
        sem = self._sems.get(key)
        if exc is None:
            self._errors.pop(key, None)
            self._flood_counts.pop(key, None)
            if key.startswith("acc:"):
                try:
                    await AccountRepo(self.db).mark_success(int(key.split(":")[1]))
                except Exception:
                    pass
            metrics.inc("backends.success")
        else:
            await self._note_account_failure(key, exc)
            if isinstance(exc, FloodWait):
                self._flood_until[key] = time.time() + exc.seconds
                metrics.inc("backends.flood")
            else:
                self._errors[key] = self._errors.get(key, 0) + 1
                metrics.inc("backends.errors")
            if self._errors.get(key, 0) >= CIRCUIT_THRESHOLD:
                until = time.time() + CIRCUIT_COOLDOWN
                self._circuit_until[key] = until
                self._errors[key] = 0
                try:
                    if key.startswith("acc:"):
                        await AccountRepo(self.db).set_circuit(int(key.split(":")[1]), until)
                except Exception:
                    pass
                metrics.inc("backends.circuit_open")
        if sem:
            sem.release()

    async def _note_account_failure(self, key: str, exc: BaseException) -> None:
        """Persist per-account failure details; auto-disable dead sessions."""
        if not key.startswith("acc:"):
            return
        try:
            account_id = int(key.split(":")[1])
        except ValueError:
            return
        name = type(exc).__name__
        msg = str(exc)[:400]
        flood_until = time.time() + exc.seconds if isinstance(exc, FloodWait) else 0.0
        permanent = name in _PERMANENT_ERROR_NAMES or "AuthKeyUnregistered" in msg or "session revoked" in msg
        try:
            if permanent:
                await AccountRepo(self.db).set_status(account_id, "unauthorized", f"{name}: {msg}")
                await AccountRepo(self.db).set_enabled(account_id, False)
                log.error("account %s disabled: %s (%s)", account_id, name, msg)
                metrics.inc("backends.disabled")
                # remove from the live pool so it is never picked again
                self._backends.pop(key, None)
                self._sems.pop(key, None)
                if isinstance(exc, FloodWait):
                    self._flood_until.pop(key, None)
            elif flood_until:
                await AccountRepo(self.db).mark_error(account_id, f"flood {exc.seconds}s", flood_until=flood_until)
            else:
                await AccountRepo(self.db).mark_error(account_id, f"{name}: {msg}")
        except Exception:
            pass

    async def note_bytes(self, key: str, nbytes: int) -> None:
        """Count actually-served bytes per backend (best-effort)."""
        if nbytes <= 0 or not (key.startswith("acc:") or key.startswith("eit:")):
            return
        repo, sid = (AccountRepo, int(key.split(":")[1])) if key.startswith("acc:") else (EitaaAccountRepo, 0)
        try:
            if repo is AccountRepo:
                await repo(self.db).note_bytes(sid, down=nbytes)
            # ponytail: eitaa stats not tracked; add eitaa_accounts byte columns if needed
        except Exception:
            pass

    def health(self) -> List[dict]:
        now = time.time()
        out: List[dict] = []
        for key, b in sorted(self._backends.items()):
            out.append(
                {
                    "key": key,
                    "kind": getattr(b, "kind", "?"),
                    "available": self._available(key),
                    "flood_until": self._flood_until.get(key, 0),
                    "circuit_until": self._circuit_until.get(key, 0),
                    "errors": self._errors.get(key, 0),
                    "sem_free": self._sem(key)._value,
                }
            )
        return out
