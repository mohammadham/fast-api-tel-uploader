"""Persistent priority queue + workers.

Download-first policy: jobs are served by (-priority, seq). Download jobs
(prio 100) always precede uploads (40). Upload workers never pick downloads,
and download workers pause uploads while any runnable download waits.
Jobs persist in SQLite (WAL) so restarts resume pending work. FloodWait
isolates the backend (TGManager) and the job retries with exponential backoff.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import heapq
import json
import logging
import os
import shutil
import socket
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis.asyncio as redis

from ..core.config import get_settings
from ..core.db import Database
from ..core.distributed import redact_redis_url
from ..core.metrics import metrics
from ..core.obs import correlation_id as corr_var, job_id as job_var, slog
from ..core.settings_service import get_runtime, runtime_settings
from ..core.models import (
    AccountRepo,
    BotRepo,
    FileRepo,
    FolderRepo,
    Job,
    JobRepo,
    KIND_DELETE,
    KIND_DOWNLOAD,
    KIND_TRANSFER,
    KIND_UPLOAD,
    PRIO_DELETE,
    PRIO_DOWNLOAD,
    PRIO_UPLOAD,
    new_id,
    now,
)
from ..services.access import AccessService, normalize_chat
from ..tg.base import FloodWait, SendFailure, TransferError
from ..tg.manager import NoBackendAvailable, TGManager
from .scheduler import is_overdue, job_size, lane_for, pick_best

log = logging.getLogger("tgdrive.queue")
slog_q = slog("tgdrive.queue")

RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 60.0

#: read size for the content digest (1 MiB keeps memory flat on huge files)
SHA256_CHUNK = 1024 * 1024


def sha256_file(path: str, chunk: int = SHA256_CHUNK) -> str:
    """Hex sha256 of a file, read in chunks so a multi-GB upload never lands in RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while blk := fh.read(chunk):
            h.update(blk)
    return h.hexdigest()


def _gzip_copy(src: str, dst: str) -> int:
    """Compress src to a temporary .gz and return the physical byte count.

    Streaming (not ``gzip.compress(data)``) keeps memory flat for multi-GB
    uploads.  A part that does not get smaller is discarded by the caller.
    """
    with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)
    return os.path.getsize(dst)


class QueueManager:
    def __init__(self, db: Database, manager: TGManager, bot_service=None, node_id: str = "") -> None:
        self.db = db
        self.manager = manager
        self.access = AccessService(db, manager)
        self.bot_service = bot_service
        self.settings = get_settings()
        self.node_id = node_id or os.environ.get("TGDRIVE_NODE_ID") or socket.gethostname()
        self.jobs = JobRepo(db)
        self.files = FileRepo(db)
        # kept for compatibility with older tooling/tests; the live claim path
        # no longer uses an in-process heap (one DB-backed path for every engine)
        self._heap: List[Tuple[int, int, str]] = []
        self._seq_floor: int = 0
        self._rows: Dict[str, Dict[str, Any]] = {}  # job_id → latest row snapshot
        self._wake = asyncio.Event()
        self._paused_kinds: set[str] = set()
        self._workers: List[asyncio.Task] = []
        self._worker_counts = {"download": 0, "upload": 0}
        self._worker_seq = {"download": 0, "upload": 0}
        self._stopped = False
        # job_id → backend key (acc:1 / bot:2) that handled it, set by handlers
        # via self._handled_by and consumed in _process after a job completes
        self._last_backend_by_job: Dict[str, str] = {}
        # global upload concurrency gate (runtime setting max_concurrent_uploads)
        self._upload_gate: Optional[asyncio.Semaphore] = None
        self._upload_gate_val: int = 0
        # admin alerting: upload-queue stall detection + per-backend flood tracker
        self._stall_since: Optional[float] = None
        self._stall_notified: bool = False
        self._stall_clear_notified: bool = True
        # job_id → wait-started-at for uploads currently blocked acquiring the
        # global upload gate (they are status=running but stuck behind the cap)
        self._gate_waiters: Dict[str, float] = {}
        # job_id → live upload progress/account snapshot for the panel
        self._upload_progress: Dict[str, Dict[str, Any]] = {}
        self._redis: Optional[redis.Redis] = None
        self._redis_url: Optional[str] = None
        self._redis_ping_done = False
        # a dead Redis must not stall every enqueue: remember the last failure
        # and skip re-pinging the same URL for a short cooldown
        self._redis_failed_url: str = ""
        self._redis_failed_at: float = 0.0
        # distributed claim leases: job_id -> opaque owner token
        self._redis_leases: Dict[str, str] = {}
        self._redis_wake_task: Optional[asyncio.Task] = None
        self._lease_task: Optional[asyncio.Task] = None
        self._hb_task: Optional[asyncio.Task] = None
        self._stall_task: Optional[asyncio.Task] = None

    async def _redis_settings(self) -> Tuple[bool, str]:
        """(enabled, url) from runtime DB settings, falling back to env.

        The Redis queue backend is runtime-configurable (not env-only), so the
        panel can enable/disable/repoint Redis without a restart. TTL-cached via
        runtime_settings() so this stays cheap on every enqueue.
        """
        from ..core.settings_service import runtime_settings

        enabled = bool(self.settings.redis_enabled)
        url = (self.settings.redis_url or "").strip()
        try:
            vals = await runtime_settings().get_all(self.db, force=False)
            if "redis_enabled" in vals:
                enabled = bool(int(vals.get("redis_enabled") or 0))
            if str(vals.get("redis_url") or "").strip():
                url = str(vals.get("redis_url")).strip()
        except Exception:
            pass
        return enabled, url

    async def _redis_active(self) -> bool:
        enabled, url = await self._redis_settings()
        return bool(enabled and url)

    async def _ensure_redis(self) -> None:
        """(Re)connect to Redis when current runtime settings enable it.

        Re-reads enabled/URL from the settings table each call so panel changes
        take effect without a restart; falls back to SQLite on any failure.
        """
        enabled, url = await self._redis_settings()
        if not enabled or not url:
            if self._redis is not None:
                try:
                    await self._redis.aclose()
                except Exception:
                    pass
            self._redis = None
            self._redis_url = None
            self._redis_ping_done = True
            return
        if self._redis is None or self._redis_url != url:
            # down Redis: don't pay a connect timeout on every single job
            if (
                self._redis_failed_url == url
                and (time.time() - self._redis_failed_at) < 30
            ):
                return
            if self._redis is not None:
                try:
                    await self._redis.aclose()
                except Exception:
                    pass
            self._redis = redis.from_url(
                url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
            self._redis_url = url
            self._redis_ping_done = False
        try:
            await self._redis.ping()
            self._redis_ping_done = True
            self._redis_failed_url = ""
            log.info("Redis queue backend enabled at %s", redact_redis_url(url))
        except Exception as e:
            log.warning("Redis unavailable (%s), falling back to SQLite only", e)
            self._redis = None
            self._redis_ping_done = True
            self._redis_failed_url = url
            self._redis_failed_at = time.time()

    # ── lifecycle ──────────────────────────────────────────────
    async def start(self) -> None:
        self._stopped = False
        await self._recover()
        # worker counts: runtime DB settings win over env config
        dl, ul = self.settings.download_workers, self.settings.upload_workers
        try:
            from ..core.settings_service import runtime_settings

            vals = await runtime_settings().get_all(self.db, force=True)
            dl = int(vals.get("download_workers") or dl)
            ul = int(vals.get("upload_workers") or ul)
        except Exception:
            pass
        self.set_worker_counts(dl, ul)
        self._redis_wake_task = asyncio.create_task(self._redis_wake_loop())
        self._lease_task = asyncio.create_task(self._lease_loop())
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        self._stall_task = asyncio.create_task(self._stall_loop())
        log.info("queue started (node=%s) with %s workers", self.node_id, len(self._workers))

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        bg = (self._lease_task, self._hb_task, self._stall_task, self._redis_wake_task)
        for t in self._workers + [t for t in bg if t]:
            t.cancel()
        for t in self._workers + [t for t in bg if t]:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()

    # ── dynamic worker resizing ───────────────────────────────
    async def _upload_semaphore(self) -> asyncio.Semaphore:
        """Global upload gate; rebuilt lazily when the runtime setting changes."""
        cap = self.settings.max_concurrent_uploads
        try:
            cap = int(await get_runtime(self.db, "max_concurrent_uploads") or cap)
        except Exception:
            pass
        cap = max(1, cap)
        if self._upload_gate is None or self._upload_gate_val != cap:
            self._upload_gate = asyncio.Semaphore(cap)
            self._upload_gate_val = cap
        return self._upload_gate

    def set_worker_counts(self, download: int, upload: int) -> None:
        """Grow/shrink worker pools live (no restart needed)."""
        download = max(1, int(download))
        upload = max(1, int(upload))
        for kind, target in (("download", download), ("upload", upload)):
            cur = self._worker_counts[kind]
            prefix = "dl" if kind == "download" else "ul"
            alive = [t for t in self._workers if not t.done() and t.get_name().startswith(prefix)]
            if target > len(alive):
                for _ in range(target - len(alive)):
                    self._worker_seq[kind] += 1
                    name = f"{prefix}{self._worker_seq[kind]}"
                    self._workers.append(asyncio.create_task(self._worker_loop(kind, name), name=name))
            elif target < len(alive):
                # mark excess tasks; they exit after their current job finishes
                to_stop = len(alive) - target
                for t in alive:
                    if to_stop <= 0:
                        break
                    if not t.get_name().startswith("stop-"):
                        t.set_name(f"stop-{t.get_name()}")
                        to_stop -= 1
            self._worker_counts[kind] = target
        self._workers = [t for t in self._workers if not t.done()]
        self._wake.set()

    def worker_counts(self) -> Dict[str, int]:
        return dict(self._worker_counts)

    # ── redis wake bus + distributed leases ───────────────────
    async def _publish_wake(self, reason: str) -> None:
        """Tell other nodes to re-scan immediately (best-effort)."""
        if self._redis is None:
            return
        try:
            await self._redis.publish("queue:wake", reason)
        except Exception as exc:
            log.debug("redis wake publish failed: %s", exc)

    async def _redis_wake_loop(self) -> None:
        """Subscribe to the shared wake channel; reconnect with backoff."""
        while not self._stopped:
            pubsub = None
            try:
                if self._redis is None:
                    if await self._redis_active():
                        await self._ensure_redis()
                    if self._redis is None:
                        await asyncio.sleep(5)
                        continue
                pubsub = self._redis.pubsub()
                await pubsub.subscribe("queue:wake")
                # catch up on anything enqueued while we were reconnecting
                self._wake.set()
                async for msg in pubsub.listen():
                    if self._stopped:
                        break
                    if msg and msg.get("type") == "message":
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("redis wake listener failed: %s", exc)
                await asyncio.sleep(2)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.close()
                    except Exception:
                        pass

    @staticmethod
    def _redis_lease_key(job_id: str) -> str:
        return f"queue:lease:{job_id}"

    async def _redis_claim_lease(self, job_id: str, token: str, ttl_s: int) -> bool:
        """Best-effort distributed guard before the authoritative DB UPDATE."""
        if self._redis is None:
            return True
        try:
            ok = await self._redis.set(
                self._redis_lease_key(job_id), token,
                nx=True, px=max(1000, int(ttl_s) * 1000),
            )
            return bool(ok)
        except Exception as exc:
            # DB CAS still decides; never stall the queue on Redis
            log.debug("redis lease claim failed: %s", exc)
            return True

    async def _redis_release_lease(self, job_id: str, token: str) -> None:
        if self._redis is None:
            return
        script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        try:
            await self._redis.eval(script, 1, self._redis_lease_key(job_id), token)
        except Exception as exc:
            log.debug("redis lease release failed: %s", exc)

    async def _redis_renew_lease(self, job_id: str, token: str, ttl_s: int) -> bool:
        if self._redis is None:
            return False
        script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('PEXPIRE', KEYS[1], ARGV[2]) else return 0 end"
        )
        try:
            return bool(await self._redis.eval(
                script, 1, self._redis_lease_key(job_id), token, max(1000, int(ttl_s) * 1000)
            ))
        except Exception as exc:
            log.debug("redis lease renew failed: %s", exc)
            return False

    # ── lease renewal + node heartbeat (multi-server) ─────────
    async def _lease_loop(self) -> None:
        """Renew leases of running jobs so other nodes don't steal them."""
        while not self._stopped:
            try:
                lease = int((await self._policy())[3])
                running = [jid for jid, r in self._rows.items() if r.get("status") == "running"]
                if running:
                    await self.jobs.renew_leases(running, self.node_id, lease)
                for job_id, token in list(self._redis_leases.items()):
                    if not await self._redis_renew_lease(job_id, token, lease):
                        self._redis_leases.pop(job_id, None)
            except Exception as exc:
                log.debug("lease renewal failed: %s", exc)
            await asyncio.sleep(60)

    async def _stall_loop(self) -> None:
        """Frequent upload-stall check: a stalled queue should alert admins
        within seconds of crossing the threshold, not on the 30s heartbeat."""
        while not self._stopped:
            try:
                await self.check_upload_stall()
            except Exception as exc:
                log.debug("upload stall check failed: %s", exc)
            await asyncio.sleep(5)

    async def _heartbeat_loop(self) -> None:
        """Upsert this node into the nodes table (multi-server dashboard)."""
        version = ""
        try:
            from app.main import app as _app

            version = str(getattr(_app, "version", ""))
        except Exception:
            pass
        while not self._stopped:
            try:
                insert_sql = (
                    "INSERT INTO nodes(node_id, hostname, version, started_at, last_heartbeat, workers_dl, workers_ul)"
                    " VALUES(?,?,?,?,?,?,?)"
                )
                if getattr(self.db, "is_mysql", False):
                    insert_sql += (
                        " ON DUPLICATE KEY UPDATE hostname=VALUES(hostname), version=VALUES(version),"
                        " last_heartbeat=VALUES(last_heartbeat), workers_dl=VALUES(workers_dl),"
                        " workers_ul=VALUES(workers_ul)"
                    )
                else:
                    insert_sql += (
                        " ON CONFLICT(node_id) DO UPDATE SET hostname=excluded.hostname, version=excluded.version,"
                        " last_heartbeat=excluded.last_heartbeat, workers_dl=excluded.workers_dl,"
                        " workers_ul=excluded.workers_ul"
                    )
                await self.db.execute(
                    insert_sql,
                    (
                        self.node_id,
                        socket.gethostname(),
                        version,
                        time.time(),
                        time.time(),
                        self._worker_counts["download"],
                        self._worker_counts["upload"],
                    ),
                )
            except Exception as exc:
                log.debug("node heartbeat failed: %s", exc)
            await asyncio.sleep(30)

    async def _recover(self) -> None:
        """Load durable jobs and release only leases this node owns/expired.

        The DB is the single source of truth for claiming. Redis is a mirror:
        stale ``queue:ids`` entries (completed/deleted jobs, or ids missing from
        the DB) are cleaned instead of being resurrected as phantom pending
        work. Multi-node restarts must never reset another node's live lease.
        """
        rows = await self.db.fetch_all(
            "SELECT * FROM jobs WHERE status IN ('pending','retry','running','lease')"
        )
        db_ids = {str(r["id"]) for r in rows}
        if await self._redis_active():
            try:
                await self._ensure_redis()
                if self._redis_ping_done and self._redis is not None:
                    job_ids = await self._redis.smembers("queue:ids")
                    cleaned = 0
                    for job_id in job_ids:
                        if str(job_id) not in db_ids:
                            # job finished, was purged, or never reached the DB
                            await self._redis.srem("queue:ids", job_id)
                            await self._redis.delete(f"queue:job:{job_id}")
                            cleaned += 1
                    log.info(
                        "queue redis mirror checked: %s ids, %s stale removed",
                        len(job_ids), cleaned,
                    )
            except Exception as e:
                log.warning("Redis recovery check failed (DB remains primary): %s", e)
        if self._redis is not None:
            await self._publish_wake("recover")

        t = now()
        recovered = 0
        for row in rows:
            status = str(row["status"])
            if status in ("running", "lease"):
                owner = str(row.get("lease_owner") or "")
                expired = float(row.get("lease_until") or 0) < t
                mine = (not owner) or owner.startswith(f"{self.node_id}/")
                if not (expired or mine):
                    # another live node owns this job: observe, never re-queue
                    self._rows[row["id"]] = {**row, "size_bytes": job_size(row)}
                    continue
                await self.jobs.update_fields(
                    row["id"], status="pending", lease_owner="", lease_until=0
                )
                row = {**row, "status": "pending", "lease_owner": "", "lease_until": 0}
            row["size_bytes"] = job_size(row)
            self._rows[row["id"]] = row
            recovered += 1
        log.info("queue recovered %s runnable/observed jobs (DB primary)", recovered)

    # ── enqueue / control ──────────────────────────────────────
    async def enqueue(self, kind: str, payload: Dict[str, Any], priority: int, max_retries: Optional[int] = None) -> str:
        # Monotonic per-process sequence: several enqueues in the same millisecond
        # must never share seq (they would fall back to a random job-id tie-break).
        seq = max(int(time.time() * 1000), self._seq_floor + 1)
        self._seq_floor = seq
        size_bytes = job_size({"payload": payload})
        corr = corr_var.get("")  # propagate caller's correlation-id into the job
        job = Job(
            id=new_id("j"),
            kind=kind,
            priority=priority,
            seq=seq,
            correlation_id=corr,
            payload=payload,
            max_retries=max_retries if max_retries is not None else self.settings.job_max_retries,
            created_at=now(),
        )
        # snapshot retry policy + origin node (sticky uploads in multi-server mode)
        try:
            runtime_retries = int(await get_runtime(self.db, "job_max_retries"))
            if runtime_retries and runtime_retries > 0:
                job.max_retries = runtime_retries
        except Exception:
            pass
        job.origin_node = self.node_id
        await self.jobs.insert(job)
        self._rows[job.id] = {
            "id": job.id, "kind": kind, "priority": priority, "seq": seq, "correlation_id": corr,
            "status": "pending", "payload": json.dumps(payload), "attempts": 0,
            "max_retries": job.max_retries, "next_run_at": 0, "created_at": job.created_at,
            "size_bytes": size_bytes, "lease_owner": "", "lease_until": 0.0,
        }
        self._wake.set()
        metrics.inc(f"queue.enqueued.{kind}")
        # Also store in Redis for scaling if enabled (lazy init)
        if await self._redis_active():
            try:
                await self._ensure_redis()
                if self._redis is None:  # unreachable (cooldown) → SQLite only
                    raise RuntimeError("redis unavailable")
                key = f"queue:job:{job.id}"
                await self._redis.hset_mapping(key, mapping={
                    "id": job.id, "kind": kind, "priority": priority, "seq": seq,
                    "correlation_id": corr, "status": "pending",
                    "payload": json.dumps(payload), "attempts": 0,
                    "max_retries": job.max_retries,
                })
                await self._redis.sadd("queue:ids", job.id)
            except Exception as e:
                log.debug("Redis enqueue write failed, using SQLite only: %s", e)
        if self._redis is not None:
            await self._publish_wake(f"enqueue:{job.id}")
        slog_q.info(
            "job enqueued",
            kind=kind,
            priority=priority,
            **({"file_id": payload["file_id"]} if payload.get("file_id") else {}),
            **({"correlation_id": corr} if corr else {}),
        )
        return job.id

    def pause(self, kind: str) -> None:
        self._paused_kinds.add(kind)
        self._wake.set()

    def resume(self, kind: str) -> None:
        self._paused_kinds.discard(kind)
        self._wake.set()

    def paused(self) -> List[str]:
        return sorted(self._paused_kinds)
    def unenqueue_deleted_job(self, job_id: str) -> None:
        """Remove a canceled upload's leftover job from the queue + jobs table."""
        self._rows.pop(job_id, None)

    # ── worker loops ───────────────────────────────────────────
    async def _policy(self) -> Tuple[int, int, int, int]:
        """(small_max_bytes, max_wait_s, scan_limit, lease_s) from runtime DB."""
        s = self.settings
        try:
            vals = await runtime_settings().get_all(self.db)
        except Exception:
            vals = {}
        small = int(vals.get("scheduler_small_max_bytes") or s.scheduler_small_max_bytes or 0)
        max_wait = int(vals.get("scheduler_max_wait_s") or s.scheduler_max_wait_s or 0)
        scan = int(vals.get("scheduler_scan_limit") or s.scheduler_scan_limit or 200)
        lease = int(vals.get("job_lease_seconds") or s.job_lease_seconds or 1800)
        return max(0, small), max(0, max_wait), max(20, min(5000, scan)), max(60, lease)

    async def _claim_next(self, worker_kind: str) -> Optional[Dict[str, Any]]:
        """Claim one job, engine-agnostically, with size/age-aware ordering.

        The candidate window is bounded by scheduler_scan_limit; the final
        choice is made by app/queue/scheduler.py. The UPDATE is the atomic
        compare-and-set, so two nodes may scan the same window but only one
        wins a given job. A lost race retries instead of killing the worker.
        """
        while not self._stopped:
            small, max_wait, scan, lease = await self._policy()
            conds = [
                "(status IN ('pending','retry') AND next_run_at <= ?)",
                "(status IN ('running','lease') AND lease_until < ?)",
            ]
            params: List[Any] = [now(), now()]
            filters: List[str] = []
            if worker_kind == "upload":
                filters.append("kind IN (?, ?)")
                params.extend([KIND_UPLOAD, KIND_TRANSFER])
            if self._paused_kinds:
                ph = ",".join("?" for _ in self._paused_kinds)
                filters.append(f"kind NOT IN ({ph})")
                params.extend(sorted(self._paused_kinds))
            if worker_kind == "upload":
                filters.append("(origin_node = ? OR origin_node = '')")
                params.append(self.node_id)
            where = "(" + " OR ".join(conds) + ")"
            if filters:
                where += " AND " + " AND ".join(filters)
            sql = f"SELECT * FROM jobs WHERE {where} ORDER BY priority DESC, seq LIMIT ?"
            params.append(scan)
            try:
                rows = await self.db.fetch_all(sql, params)
            except Exception as exc:
                log.warning("job scan failed: %s", exc)
                rows = []
            if rows:
                chosen = pick_best(
                    rows, now=now(), max_wait_s=max_wait, small_max_bytes=small
                )
                if chosen is not None:
                    owner = f"{self.node_id}/{worker_kind[:2]}"
                    token = f"{owner}:{time.time_ns()}"
                    if not await self._redis_claim_lease(chosen["id"], token, lease):
                        continue  # another node already owns the distributed lease
                    lease_until = now() + lease
                    try:
                        affected = await self.db.execute(
                            "UPDATE jobs SET status='running', lease_owner=?, lease_until=?, updated_at=?"
                            " WHERE id=? AND (status IN ('pending','retry')"
                            " OR (status IN ('running','lease') AND lease_until < ?))",
                            (owner, lease_until, now(), chosen["id"], now()),
                        )
                    except Exception:
                        await self._redis_release_lease(chosen["id"], token)
                        raise
                    if affected:
                        if self._redis is not None:
                            self._redis_leases[chosen["id"]] = token
                        claimed = {**chosen, "status": "running", "lease_owner": owner, "lease_until": lease_until}
                        self._rows[chosen["id"]] = claimed
                        return claimed
                    await self._redis_release_lease(chosen["id"], token)
                    continue  # another worker/node won this one
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        return None

    async def _worker_loop(self, worker_kind: str, name: str) -> None:
        while not self._stopped:
            if name.startswith("stop-"):
                return  # worker retired by set_worker_counts
            try:
                row = await self._claim_next(worker_kind)
                if row is None:
                    if self._stopped:
                        return
                    await asyncio.sleep(0.2)
                    continue
                await self._process(row, name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # a single broken job/claim must never silently shrink the pool
                log.exception("worker %s crashed, continuing: %s", name, exc)
                await asyncio.sleep(1.0)

    async def _next_job_pg(self, worker_kind: str) -> Optional[Dict[str, Any]]:
        """Multi-node claim path: atomic SKIP-LOCKED-style UPDATE ... RETURNING.

        Upload jobs are sticky to their origin node (tmp file lives there);
        expired leases from dead nodes may be stolen by any node.
        """
        from ..core.models import KIND_DOWNLOAD, KIND_UPLOAD, now as _now

        prefix = "dl" if worker_kind == "download" else "ul"
        while not self._stopped:
            if prefix == "ul" and KIND_DOWNLOAD in self._paused_kinds:
                pass
            conds = [
                "(status IN ('pending','retry') AND next_run_at <= ?)",
                "(status IN ('running','lease') AND lease_until < ?)",
            ]
            params: List[Any] = [now(), now()]
            # Hard filters: AND'ed, never OR'ed — an OR-joined kind filter
            # would make the WHERE unconditional and re-claim finished jobs.
            filters: List[str] = []
            if worker_kind == "upload":
                # uploads + transfers share the upload worker pool (same
                # telegram pressure profile)
                filters.append("kind IN (?, ?)")
                params.extend([KIND_UPLOAD, KIND_TRANSFER])
            if self._paused_kinds:
                ph = ",".join("?" for _ in self._paused_kinds)
                filters.append(f"kind NOT IN ({ph})")
                params.extend(sorted(self._paused_kinds))
            if prefix == "ul":
                filters.append("(origin_node = ? OR origin_node = '')")
                params.append(self.node_id)
            where = " OR ".join(conds) + (" AND " + " AND ".join(filters) if filters else "")
            sql = (
                "UPDATE jobs SET status='running', lease_owner=?, lease_until=?, updated_at=?"
                " WHERE id = (SELECT id FROM jobs WHERE " + where +
                " ORDER BY priority DESC, seq LIMIT 1)"
                " RETURNING *"
            )
            full_params = [f"{self.node_id}/{prefix}", now() + 1800, now()] + params
            try:
                row = await self.db.fetch_one(sql, full_params)
            except Exception as exc:
                log.debug("pg claim failed: %s", exc)
                row = None
            if row:
                self._rows[row["id"]] = dict(row)
                return dict(row)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        return None

    async def _next_job_mysql(self, worker_kind: str) -> Optional[Dict[str, Any]]:
        """Multi-node claim path for MySQL: lock a row, then update it.

        MySQL has no ``UPDATE ... RETURNING``, so the candidate is selected
        ``FOR UPDATE SKIP LOCKED`` and updated inside the same transaction.
        MySQL < 8 / MariaDB < 10.6 lack SKIP LOCKED: the claim falls back to a
        plain ``FOR UPDATE`` (more contention, same correctness).
        """
        from ..core.models import KIND_DOWNLOAD, KIND_UPLOAD, now as _now

        prefix = "dl" if worker_kind == "download" else "ul"
        lock = " FOR UPDATE SKIP LOCKED"
        while not self._stopped:
            conds = [
                "(status IN ('pending','retry') AND next_run_at <= ?)",
                "(status IN ('running','lease') AND lease_until < ?)",
            ]
            params: List[Any] = [now(), now()]
            # Hard filters: AND'ed, never OR'ed — an OR-joined kind filter
            # would make the WHERE unconditional and re-claim finished jobs.
            filters: List[str] = []
            if worker_kind == "upload":
                # uploads + transfers share the upload worker pool (same
                # telegram pressure profile)
                filters.append("kind IN (?, ?)")
                params.extend([KIND_UPLOAD, KIND_TRANSFER])
            if self._paused_kinds:
                ph = ",".join("?" for _ in self._paused_kinds)
                filters.append(f"kind NOT IN ({ph})")
                params.extend(sorted(self._paused_kinds))
            if prefix == "ul":
                filters.append("(origin_node = ? OR origin_node = '')")
                params.append(self.node_id)
            where = " OR ".join(conds) + (" AND " + " AND ".join(filters) if filters else "")
            select_sql = (
                "SELECT * FROM jobs WHERE " + where +
                " ORDER BY priority DESC, seq LIMIT 1" + lock
            )
            update_sql = (
                "UPDATE jobs SET status='running', lease_owner=?, lease_until=?, updated_at=? WHERE id=?"
            )
            row: Optional[Dict[str, Any]] = None
            try:
                async with self.db.tx() as tx:
                    row = await tx.fetch_one(select_sql, params)
                    if row:
                        lease_owner = f"{self.node_id}/{prefix}"
                        lease_until = now() + 1800
                        await tx.execute(update_sql, [lease_owner, lease_until, now(), row["id"]])
                        row = {**row, "status": "running",
                               "lease_owner": lease_owner, "lease_until": lease_until}
            except Exception as exc:
                log.debug("mysql claim failed: %s", exc)
                if lock and "syntax" in str(exc).lower():
                    lock = ""
                row = None
            if row:
                self._rows[row["id"]] = dict(row)
                return dict(row)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        return None

    async def _next_job(self, worker_kind: str) -> Optional[Dict[str, Any]]:
        """Block until a runnable job for this worker kind is available."""
        while not self._stopped:
            deferred: List[Tuple[int, int, str]] = []
            chosen: Optional[Dict[str, Any]] = None
            while self._heap:
                negprio, seq, job_id = heapq.heappop(self._heap)
                row = self._rows.get(job_id)
                if row is None:
                    continue
                if row["status"] not in ("pending", "retry"):
                    continue  # stale duplicate entry
                entry = (-int(row["priority"]), int(row["seq"]), job_id)
                if row["next_run_at"] > now():
                    deferred.append(entry)
                    continue
                if row["kind"] in self._paused_kinds:
                    deferred.append(entry)
                    continue
                if worker_kind == "upload" and row["kind"] == KIND_DOWNLOAD:
                    deferred.append(entry)
                    continue
                # transfers share the upload pool (same pressure profile) but
                # downloads-first starvation guard does not apply to them
                if worker_kind == "download" and row["kind"] == KIND_TRANSFER:
                    deferred.append(entry)
                    continue
                if worker_kind == "download" and row["kind"] == KIND_UPLOAD and self._downloads_waiting():
                    deferred.append(entry)
                    continue
                chosen = row
                break
            for entry in deferred:
                heapq.heappush(self._heap, entry)
            if chosen is not None:
                # Atomic claim: UPDATE only if job is still pending/retry, then check rows affected
                rows_affected = await self.db.execute(
                    "UPDATE jobs SET status='running', lease_owner=?, lease_until=?, updated_at=? WHERE id=? AND status IN ('pending','retry')",
                    (f"{self.node_id}/{chosen['kind'][:2]}?", now() + 1800, now(), chosen["id"]),
                )
                if rows_affected == 0:
                    # Job was claimed by another worker; put it back at end of heap
                    chosen = None
                else:
                    chosen["status"] = "running"
                return chosen
            # nothing runnable now: wait for wake-up or retry timer
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        return None

    def _downloads_waiting(self) -> bool:
        for row in self._rows.values():
            if (
                row["kind"] == KIND_DOWNLOAD
                and row["status"] in ("pending", "retry")
                and row["next_run_at"] <= now()
            ):
                return True
        return False

    # ── admin alerts: stalled upload queue + repeated floods ───
    def _notify_admins_sync(self, text: str) -> None:
        """Fire-and-forget admin notification (never blocks the queue)."""
        from ..services.notify import notify_admins

        asyncio.get_running_loop().create_task(self._safe_notify(text))

    async def _safe_notify(self, text: str) -> None:
        try:
            from ..services.notify import notify_admins

            await notify_admins(text)
        except Exception:
            pass

    async def check_upload_stall(self) -> None:
        """Detect an upload queue stuck behind the concurrency gate and alert.

        A stall = ≥1 runnable upload/transfer waiting while all upload workers
        are busy (or all backends flooded) for longer than
        upload_stall_threshold_s. Recovering clears the banner; crossing the
        threshold again re-alerts (unlike the flood alert, this one is cheap —
        it means real uploads are piled up).
        """
        if self._stopped:
            return
        try:
            threshold = int(await get_runtime(self.db, "upload_stall_threshold_s") or 0)
        except Exception:
            threshold = 0
        if threshold <= 0:
            self._stall_since = None
            self._stall_notified = False
            return
        t = now()
        waiting_rows = self._waiting_uploads()
        waiting = len(waiting_rows) + len(self._gate_waiters)
        oldest = 0.0
        for row in waiting_rows:
            # waiting since the later of (enqueued, became-runnable-after-retry)
            age = t - max(float(row.get("next_run_at") or 0), float(row.get("created_at") or 0))
            if age > oldest:
                oldest = age
        gw_age = self._gate_wait_age()
        if gw_age > oldest:
            oldest = gw_age
        stalled = waiting > 0 and oldest >= threshold
        if stalled:
            # the job's own age measures the persistence — no extra clock needed
            if self._stall_since is None:
                self._stall_since = t
            if not self._stall_notified:
                self._stall_notified = True
                self._stall_clear_notified = False
                metrics.inc("queue.stall_alert")
                self._notify_admins_sync(
                    f"⚠ صف آپلود معطل شده: {waiting} جاب پشت سقف همزمانی (قدیمی‌ترین {int(oldest)} ثانیه)"
                )
                slog_q.warning("upload queue stalled", waiting=waiting, oldest_s=round(oldest, 1))
        else:
            if self._stall_since is not None and not self._stall_clear_notified:
                self._stall_clear_notified = True
                self._notify_admins_sync("✅ صف آپلود رفع معطلی شد")
            self._stall_since = None
            self._stall_notified = False

    def _gate_wait_age(self) -> float:
        """Longest current wait behind the upload gate (0 = nobody waiting)."""
        t = time.time()
        return max([t - ts for ts in self._gate_waiters.values()], default=0.0)

    def _waiting_uploads(self) -> List[Dict[str, Any]]:
        """Upload/transfer jobs runnable right now but blocked by the global
        upload gate (or flood-isolated backends) — empty when the queue is free.
        Single source of truth for both the stall detector and the banner."""
        gate_full = self._upload_gate is not None and self._upload_gate._value <= 0
        if not gate_full and not self._all_upload_backends_flooded():
            return []
        t = now()
        out: List[Dict[str, Any]] = []
        for row in self._rows.values():
            if row.get("kind") not in (KIND_UPLOAD, KIND_TRANSFER):
                continue
            if row.get("status") == "running":
                continue
            if float(row.get("next_run_at") or 0) > t:
                continue
            out.append(row)
        return out

    def _all_upload_backends_flooded(self) -> bool:
        """True when every acc/eit backend is currently flood-isolated."""
        flooded = {
            k
            for k, until in self.manager._flood_until.items()
            if k.startswith(("acc:", "eit:")) and until > time.time()
        }
        if not flooded:
            return False
        return all(k in flooded for k in self.manager._backends if k.startswith(("acc:", "eit:")))

    # ── job processing ─────────────────────────────────────────
    async def _process(self, row: Dict[str, Any], worker_name: str) -> None:
        job_id = row["id"]
        kind = row["kind"]
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
        token = job_var.set(job_id)
        corr_token = corr_var.set(row.get("correlation_id", "") or "")
        t0 = time.perf_counter()
        try:
            try:
                if kind == KIND_DOWNLOAD:
                    await self._handle_download({**payload, "__job_id": job_id})
                elif kind == KIND_UPLOAD:
                    await self._handle_upload(payload, job_id)
                elif kind == KIND_DELETE:
                    await self._handle_delete({**payload, "__job_id": job_id})
                elif kind == KIND_TRANSFER:
                    await self._handle_transfer({**payload, "__job_id": job_id})
                else:
                    raise TransferError(f"unknown job kind {kind}")
            except FloodWait as exc:
                # Telegram told us exactly how long to wait: honour the full
                # delay and do NOT consume a retry attempt while waiting.
                await self._retry(
                    job_id, row, f"flood wait {exc.seconds}s",
                    delay=exc.seconds, consume_attempt=False,
                )
            except NoBackendAvailable as exc:
                if getattr(exc, "retryable", True):
                    await self._defer(
                        job_id, row, f"backend unavailable: {exc}",
                        getattr(exc, "retry_after", 15.0),
                    )
                else:
                    # Configuration gap (e.g. eitaa key with no eitaa account):
                    # retry with backoff, then fail the file visibly.
                    await self._retry(job_id, row, str(exc))
            except (SendFailure, TransferError) as exc:
                slog_q.warning("job failed, retrying", kind=kind, error=str(exc)[:300], attempt=row.get("attempts", 0) + 1)
                await self._retry(job_id, row, str(exc))
            except Exception as exc:  # unexpected
                slog_q.error("job crashed", kind=kind, error=str(exc)[:300])
                await self._retry(job_id, row, f"internal: {exc}")
            else:
                await self.jobs.update_fields(job_id, status="done", finished_at=now(), error="")
                # remember which backend handled this job so the panel can show
                # a per-account/per-bot 24h handled-files counter
                try:
                    bk = self._last_backend_by_job.pop(job_id, None)
                    if bk:
                        payload["handled_by"] = bk
                        await self.jobs.update_fields(job_id, payload=payload)
                except Exception:
                    pass
                self._rows.pop(job_id, None)
                metrics.inc(f"queue.done.{kind}")
                self._wake.set()
                slog_q.info(
                    "job done",
                    kind=kind,
                    worker=worker_name,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                )
        finally:
            # handlers re-set this on every attempt; dropping them here keeps the
            # maps bounded when jobs fail permanently
            self._last_backend_by_job.pop(job_id, None)
            self._upload_progress.pop(job_id, None)
            lease_token = self._redis_leases.pop(job_id, None)
            if lease_token:
                try:
                    await self._redis_release_lease(job_id, lease_token)
                except Exception:
                    pass
            job_var.reset(token)
            corr_var.reset(corr_token)

    async def _retry(
        self,
        job_id: str,
        row: Dict[str, Any],
        error: str,
        delay: Optional[float] = None,
        consume_attempt: bool = True,
    ) -> None:
        attempts = int(row.get("attempts", 0)) + (1 if consume_attempt else 0)
        kind = row["kind"]
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else {}
        if consume_attempt and attempts > int(row.get("max_retries", 5)):
            await self.jobs.update_fields(job_id, status="failed", error=error[:500], finished_at=now(), attempts=attempts)
            if kind == KIND_UPLOAD and payload.get("file_id"):
                await self.files.set_status(payload["file_id"], "failed", error)
                # Final failure: delete partial Telegram messages, forget the
                # part rows and remove the source plus every derived tmp path.
                await self._cleanup_final_upload(payload, error)
            self._rows.pop(job_id, None)
            metrics.inc(f"queue.failed.{kind}")
            self._wake.set()
            from ..services.notify import notify_admins

            try:
                await notify_admins(f"⛔ جاب شکست خورد: {kind}\n{error[:200]}")
            except Exception:
                pass
            return
        if delay is None:
            d = min(RETRY_BASE_DELAY * (2 ** max(0, attempts - 1)), RETRY_MAX_DELAY)
        else:
            d = max(0.5, float(delay))
            if consume_attempt:  # transient errors keep the exponential cap
                d = min(d, RETRY_MAX_DELAY)
        await self.jobs.update_fields(
            job_id, status="retry", attempts=attempts, error=error[:500],
            next_run_at=now() + d, lease_owner="", lease_until=0,
        )
        refreshed = await self.jobs.fetch(job_id)
        if refreshed:
            refreshed["size_bytes"] = job_size(refreshed)
            self._rows[job_id] = refreshed
        metrics.inc("queue.retries" if consume_attempt else "queue.deferred")
        self._wake.set()

    async def _defer(self, job_id: str, row: Dict[str, Any], error: str, delay: float) -> None:
        """Wait for a backend/flood without burning an attempt."""
        await self._retry(job_id, row, error, delay=delay, consume_attempt=False)

    # ── upload handler ─────────────────────────────────────────
    async def _record_digest(self, file_id: str, tmp_path: str) -> None:
        """Digest the source before status=ready so pollers never see an empty hash."""
        try:
            digest = await asyncio.to_thread(sha256_file, tmp_path)
            await self.files.set_sha256(file_id, digest)
        except Exception as exc:  # never fail a stored upload over a digest
            slog_q.warning("sha256 not stored", file_id=file_id, error=str(exc)[:120])

    async def _remember_tmp_paths(self, job_id: str, paths: List[str]) -> None:
        """Persist derived split/zip paths so the janitor protects active work."""
        if not job_id or not paths:
            return
        try:
            row = await self.jobs.fetch(job_id)
            raw = (row or {}).get("payload")
            payload = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw or {})
            current = list(payload.get("tmp_paths") or [])
            for path in paths:
                if path and path not in current:
                    current.append(path)
            payload["tmp_paths"] = current
            await self.jobs.update_payload(job_id, payload)
        except Exception:
            pass

    async def _record_orphans(
        self, file_id: str, rec: Dict[str, Any], parts: List[Dict[str, Any]], error: str
    ) -> None:
        """Remember Telegram messages we could not delete, for later cleanup."""
        chat = str(rec.get("storage_chat") or "me")
        for part in parts:
            try:
                await self.db.execute(
                    "INSERT INTO orphan_parts(file_id, backend, account_key, chat_key, message_id, created_at, last_error)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (
                        file_id,
                        str(rec.get("backend") or "telegram"),
                        str(part.get("account_key") or ""),
                        normalize_chat(chat),
                        int(part["message_id"]),
                        now(),
                        str(error or "")[:500],
                    ),
                )
            except Exception:
                pass

    async def _discard_parts(
        self, file_id: str, rec: Dict[str, Any], parts: List[Dict[str, Any]], reason: str
    ) -> None:
        """Best-effort delete of a partial attempt's Telegram messages."""
        if not parts:
            return
        chat = str(rec.get("storage_chat") or "me")
        preferred = next(
            (str(p.get("account_key") or "") for p in parts if p.get("account_key")), ""
        )
        try:
            borrowed = await self.access.pick(
                chat,
                backend=rec.get("backend") or "telegram",
                need="read",
                preferred=preferred,
            )
        except Exception as exc:
            await self._record_orphans(file_id, rec, parts, f"discard access: {exc}")
            return
        async with borrowed as backend:
            for part in parts:
                try:
                    await backend.delete_message(int(part["message_id"]), chat)
                except Exception as exc:
                    await self._record_orphans(file_id, rec, [part], f"discard: {exc}")

    async def _cleanup_final_upload(self, payload: Dict[str, Any], error: str) -> None:
        """Final failure: drop partial Telegram parts and every staged tmp path."""
        file_id = str(payload.get("file_id") or "")
        rec = await self.files.get(file_id) if file_id else None
        if rec:
            parts = await self.files.parts(file_id)
            if parts:
                await self._discard_parts(file_id, rec, parts, error or "final upload failure")
                await self.db.execute("DELETE FROM file_parts WHERE file_id=?", (file_id,))
        paths: List[str] = []
        for value in (payload.get("tmp_path"), payload.get("downloaded_path")):
            if value:
                paths.append(str(value))
        for value in payload.get("tmp_paths") or []:
            if value:
                paths.append(str(value))
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _prepare_upload_part(self, path: str, compress: bool) -> Tuple[str, int, str, bool]:
        """Return (send_path, stored_size, encoding, is_temporary).

        Compression is best-effort: an unreadable/expanding part falls back to
        the original bytes so a CPU/disk hiccup can never lose an upload.
        """
        original = int(os.path.getsize(path))
        if not compress or original <= 0:
            return path, original, "", False
        gz = f"{path}.gz"
        try:
            stored = await asyncio.to_thread(_gzip_copy, path, gz)
            try:
                min_save = int(
                    await get_runtime(self.db, "compress_min_savings_pct")
                    or self.settings.compress_min_savings_pct
                    or 0
                )
            except Exception:
                min_save = int(self.settings.compress_min_savings_pct or 0)
            saved_pct = ((original - stored) * 100.0 / original) if original else 0.0
            if stored < original and saved_pct >= max(0, min_save):
                return gz, stored, "gzip", True
            try:
                os.remove(gz)
            except OSError:
                pass
        except Exception as exc:
            slog_q.warning("upload compression skipped", path=path, error=str(exc)[:120])
            try:
                os.remove(gz)
            except OSError:
                pass
        return path, original, "", False

    async def _handle_upload(self, payload: Dict[str, Any], job_id: str) -> None:
        from ..core.security import decrypt_str

        file_id = payload["file_id"]
        rec = await self.files.get(file_id)
        if not rec:
            raise TransferError(f"file {file_id} missing")
        tmp_path = payload.get("tmp_path", "")
        # Idempotent retry: if a previous attempt already stored every part but
        # crashed before marking the file ready, finish the bookkeeping instead
        # of sending the whole file again to (possibly) another account.
        existing_parts = await self.files.parts(file_id)
        stored_bytes = sum(int(p.get("size") or 0) for p in existing_parts)
        # Exact equality, not >=: an over-sized/duplicated part set is corrupt
        # and must be re-uploaded, never marked ready.
        if existing_parts and stored_bytes == int(rec.get("size") or 0):
            ids = [int(p["message_id"]) for p in existing_parts]
            await self._record_digest(file_id, tmp_path)
            await self.files.set_stored(file_id, rec.get("storage_chat") or "", ids, len(ids))
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            slog_q.info("upload completed from existing parts", file_id=file_id, parts=len(ids))
            return
        if existing_parts and rec.get("status") != "ready":
            # partial previous attempt: delete its Telegram messages first, then
            # remove the rows so the fresh send cannot leave orphaned parts.
            await self._discard_parts(file_id, rec, existing_parts, "partial retry")
            await self.db.execute("DELETE FROM file_parts WHERE file_id=?", (file_id,))
        await self._maybe_store_thumb(rec, tmp_path)

        # ── resumable resume: continue from the upload session's stored offset ──
        session_id = payload.get("session_id")
        resumed_offset = int(payload.get("resumed_offset") or 0)
        if session_id:
            try:
                from ..core.models import UploadSessionRepo

                sess = await UploadSessionRepo(self.db).get(session_id)
                if sess:
                    saved = int(sess.get("offset") or 0)
                    if resumed_offset < saved:
                        resumed_offset = saved  # server is ahead → never rewind
                    if resumed_offset >= int(rec.get("size") or 0):
                        # session is already complete; run a fresh (empty) upload
                        resumed_offset = 0
                else:
                    resumed_offset = 0  # dangling session → fresh start
            except Exception:
                resumed_offset = 0
        if resumed_offset:
            slog_q.info(
                "upload resumed from offset",
                job_id=job_id,
                file_id=file_id,
                session_id=session_id,
                resumed_offset=resumed_offset,
            )

        if payload.get("tg_file_id") and payload.get("bot_token_ref") is not None:
            # Bot-relayed upload: fetch from Bot API to a temp file first.
            bot_row = await BotRepo(self.db).get(int(payload["bot_token_ref"]))
            token = decrypt_str(bot_row["token_enc"]) if bot_row else None
            base = get_settings().bot_api_base.rstrip("/")
            if not token:
                raise TransferError("bot token missing")
            async with httpx.AsyncClient(timeout=180) as http:
                info = (await http.get(f"{base}/bot{token}/getFile", params={"file_id": payload["tg_file_id"]})).json()
                if not info.get("ok"):
                    raise SendFailure(str(info.get("description", "getFile failed")))
                file_path = info["result"]["file_path"]
                with httpx.stream("GET", f"{base}/file/bot{token}/{file_path}", timeout=300) as resp:
                    resp.raise_for_status()
                    tmp_path = f"{get_settings().final_tmp_dir()}/{file_id}.dl"
                    with open(tmp_path, "wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
            payload["tmp_path"] = tmp_path
            await self.jobs.update_fields(job_id, payload=payload)  # survive retry

        if not tmp_path or not os.path.exists(tmp_path):
            raise TransferError("tmp file missing")

        name = rec["name"]
        mime = rec["mime"] or "application/octet-stream"
        size = int(rec["size"])
        t_upload = time.perf_counter()
        s = get_settings()
        try:
            split_at = int(await get_runtime(self.db, "split_threshold") or s.split_threshold)
        except Exception:
            split_at = s.split_threshold
        backend = payload.get("backend") or rec.get("backend") or "telegram"

        message_ids: List[int] = []
        # per-key storage channel (set at upload time) wins, then the file
        # record, then the backend's own default (global/per-account/Saved)
        storage_chat = (payload.get("storage_chat") or rec["storage_chat"] or "").strip()
        # folder tag caption: every path level becomes a searchable hashtag
        # (#projects #2026), synced with the file's folder at send time
        folder_tag = ""
        try:
            folder_path = (payload.get("folder_path") or "").strip()
            if not folder_path and rec.get("folder_id"):
                folder_path = await FolderRepo(self.db).path_of(int(rec["folder_id"]))
            if folder_path:
                tags = " ".join("#" + p.strip().replace(" ", "_") for p in folder_path.split("/") if p.strip())
                folder_tag = tags
        except Exception as exc:  # never fail the upload because of a caption
            slog_q.warning("folder tag skipped", file_id=file_id, error=str(exc)[:120])
        # global upload concurrency gate: caps how many uploads touch telegram
        # backends at the same time (runtime setting max_concurrent_uploads);
        # held for the whole send so uploads never exceed the configured cap
        gate = await self._upload_semaphore()
        self._gate_waiters[job_id] = time.time()
        try:
            await gate.acquire()
        finally:
            self._gate_waiters.pop(job_id, None)
        try:
            part_workers = max(1, min(16, int(
                await get_runtime(self.db, "upload_part_workers")
                or s.upload_part_workers or 4
            )))
        except Exception:
            part_workers = 4
        try:
            compress_uploads = int(await get_runtime(self.db, "compress_uploads") or 0) == 1
        except Exception:
            compress_uploads = False
        try:
            plan = await self._auto_split_plan(
                size=size, backend=backend, split_at=split_at,
                storage_chat=storage_chat,
            )
            if plan:
                account_key, auto_ids, storage_chat = await self._upload_auto_split(
                    job_id=job_id, file_id=file_id, tmp_path=tmp_path,
                    name=name, mime=mime, backend=backend, folder_tag=folder_tag,
                    part_workers=part_workers, plan=plan, compress=compress_uploads,
                )
                message_ids.extend(auto_ids)
            else:
                account_key, single_ids, storage_chat = await self._upload_single(
                    job_id=job_id, file_id=file_id, tmp_path=tmp_path,
                    name=name, mime=mime, size=size, backend=backend,
                    storage_chat=storage_chat, folder_tag=folder_tag,
                    split_at=split_at, resumed_offset=resumed_offset,
                    session_id=session_id, part_workers=part_workers,
                    compress=compress_uploads,
                )
                message_ids.extend(single_ids)
        finally:
            gate.release()

        # Content digest of the WHOLE source file, written before status=ready.
        # tmp_path is still intact here and always holds every byte (a resumed
        # upload only affects what was *sent*), so one sequential read is enough.
        await self._record_digest(file_id, tmp_path)
        await self.files.set_stored(file_id, storage_chat, message_ids, len(message_ids))
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        metrics.inc("upload.bytes", size)
        metrics.observe("upload.size", size)
        slog_q.info(
            "upload complete",
            file_id=file_id,
            size=size,
            parts=len(message_ids),
            account=account_key,
            duration_ms=round((time.perf_counter() - t_upload) * 1000, 1),
        )

        webhook = payload.get("webhook")
        if webhook:
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(webhook, json={"file_id": file_id, "status": "ready", "size": size})
            except Exception:
                slog_q.warning("webhook delivery failed", file_id=file_id, webhook=webhook[:120])

    async def _upload_single(
        self, *, job_id: str, file_id: str, tmp_path: str, name: str, mime: str,
        size: int, backend: str, storage_chat: str, folder_tag: str,
        split_at: int, resumed_offset: int, session_id: str, part_workers: int,
        compress: bool,
    ) -> Tuple[str, List[int], str]:
        """The existing one-account path (main split_threshold still applies)."""
        message_ids: List[int] = []
        target_chat = (storage_chat or "").strip()
        if target_chat:
            borrowed = await self.access.pick(
                target_chat, backend=backend, need="write"
            )
        else:
            borrowed = await self.manager.acquire("acc", backend=backend, direction="upload")
        async with borrowed as be:
            account_key = borrowed.key
            self._last_backend_by_job[job_id] = borrowed.key
            started = time.monotonic()

            def on_upload_progress(done: int, total: int, *, account=account_key) -> None:
                total = max(1, int(total or size))
                elapsed = max(0.001, time.monotonic() - started)
                self._upload_progress[job_id] = {
                    "progress_pct": min(100, round(int(done) * 100 / total)),
                    "progress_bytes": int(done),
                    "progress_total": total,
                    "speed_bps": int(int(done) / elapsed),
                    "account": account,
                    "stage": "uploading",
                    "part_workers": part_workers,
                }

            self._upload_progress[job_id] = {
                "progress_pct": 0, "progress_bytes": 0, "progress_total": size,
                "speed_bps": 0, "account": account_key, "stage": "uploading",
                "part_workers": part_workers,
            }
            # fall back to the backend default when neither the key nor the
            # record pins a chat ('' → send_document resolves 'me'/global)
            storage_chat = storage_chat or getattr(be, "storage_chat", "me") or ""
            if size > split_at and backend != "eitaa":
                # split into parts below the MTProto 2GB cap
                part_size = split_at
                part_paths = await self._split_file(tmp_path, part_size)
                await self._remember_tmp_paths(job_id, part_paths)
                try:
                    offset = 0
                    for idx, ppath in enumerate(part_paths):
                        part_name = f"{name}.part{idx:04d}"
                        part_len = os.path.getsize(ppath)
                        send_path, stored_size, encoding, compressed = await self._prepare_upload_part(ppath, compress)
                        try:
                            def part_progress(
                                done: int, _total: int, *, off=offset, plen=part_len,
                                enc=encoding, physical=stored_size,
                            ) -> None:
                                if enc == "gzip" and physical > 0:
                                    done = int(int(done) * plen / physical)
                                on_upload_progress(off + min(int(done), plen), size)

                            result = await be.send_document(
                                storage_chat, send_path, part_name, mime, caption=folder_tag,
                                part_workers=part_workers, progress_cb=part_progress,
                            )
                            mid = int(result["message_id"])
                            physical = int(result.get("size") or stored_size) or stored_size
                            message_ids.append(mid)
                            await self.files.add_part(
                                file_id, idx, mid, part_len,
                                stored_size=physical, encoding=encoding,
                                account_key=account_key,
                            )
                            offset += part_len
                        finally:
                            if compressed:
                                try:
                                    os.remove(send_path)
                                except OSError:
                                    pass
                finally:
                    for ppath in part_paths:
                        try:
                            os.remove(ppath)
                        except OSError:
                            pass
            else:
                # Telethon's send_file has no byte-offset resume; a full send
                # is honest and correct. Advancing the session prevents a
                # later retry from claiming a bogus partial offset.
                if resumed_offset:
                    slog_q.warning(
                        "upload resume requested but backend sends full file",
                        file_id=file_id, requested_offset=resumed_offset,
                    )
                send_path, stored_size, encoding, compressed = await self._prepare_upload_part(tmp_path, compress)
                try:
                    def single_progress(
                        done: int, total: int, *, enc=encoding, physical=stored_size,
                    ) -> None:
                        if enc == "gzip" and physical > 0:
                            done = int(int(done) * size / physical)
                        on_upload_progress(done, size)

                    result = await be.send_document(
                        storage_chat, send_path, name, mime, caption=folder_tag,
                        part_workers=part_workers, progress_cb=single_progress,
                    )
                    mid = int(result["message_id"])
                    physical = int(result.get("size") or stored_size) or stored_size
                    message_ids.append(mid)
                    await self.files.add_part(
                        file_id, 0, mid, size,
                        stored_size=physical, encoding=encoding,
                        account_key=account_key,
                    )
                finally:
                    if compressed:
                        try:
                            os.remove(send_path)
                        except OSError:
                            pass
                if session_id:
                    try:
                        from ..core.models import UploadSessionRepo

                        await UploadSessionRepo(self.db).advance(session_id, size)
                    except Exception:
                        pass
        if storage_chat and account_key:
            await self.access.mark(
                account_key, storage_chat, backend,
                can_read=True, can_write=True, source="upload",
            )
        return account_key, message_ids, storage_chat

    async def _upload_auto_split(
        self, *, job_id: str, file_id: str, tmp_path: str, name: str, mime: str,
        backend: str, folder_tag: str, part_workers: int, plan: Dict[str, Any],
        compress: bool,
    ) -> Tuple[str, List[int], str]:
        """Split one file into balanced parts and upload them on separate accounts.

        Each part acquires its own upload slot and releases it immediately, so
        the pool decides which free account carries which part. The outer job
        still holds one global upload-gate slot; ``plan['concurrency']`` bounds
        how many parts run at once.
        """
        parts = int(plan["parts"])
        part_paths = await self._split_file_balanced(tmp_path, parts)
        await self._remember_tmp_paths(job_id, part_paths)
        if len(part_paths) != parts:
            raise TransferError(f"auto-split produced {len(part_paths)}/{parts} parts")
        part_sizes = [os.path.getsize(p) for p in part_paths]
        done = [0] * parts
        message_ids = [0] * parts
        used_accounts: set[str] = set()
        account_lock = asyncio.Lock()
        planned_accounts = list(plan.get("account_keys") or [])
        sem = asyncio.Semaphore(max(1, int(plan.get("concurrency") or 1)))
        started = time.monotonic()

        def publish(account: str) -> None:
            done_total = sum(min(done[i], part_sizes[i]) for i in range(parts))
            elapsed = max(0.001, time.monotonic() - started)
            self._upload_progress[job_id] = {
                "progress_pct": min(100, round(done_total * 100 / max(1, sum(part_sizes)))),
                "progress_bytes": done_total,
                "progress_total": sum(part_sizes),
                "speed_bps": int(done_total / elapsed),
                "account": account or "auto",
                "stage": "uploading",
                "part_workers": part_workers,
                "auto_split": True,
                "parts_total": parts,
                "parts_done": sum(1 for i in range(parts) if done[i] >= part_sizes[i]),
                "accounts_used": len(used_accounts),
            }

        self._upload_progress[job_id] = {
            "progress_pct": 0, "progress_bytes": 0, "progress_total": sum(part_sizes),
            "speed_bps": 0, "account": "auto", "stage": "uploading",
            "part_workers": part_workers, "auto_split": True,
            "parts_total": parts, "parts_done": 0, "accounts_used": 0,
        }

        async def send_part(idx: int, ppath: str, part_len: int) -> None:
            send_path, stored_size, encoding, compressed = await self._prepare_upload_part(ppath, compress)
            try:
                async with sem:
                    # Spread the first parts over distinct authorized accounts;
                    # once that is achieved the normal pool may reuse accounts.
                    async with account_lock:
                        want_distinct = min(len(planned_accounts), parts)
                        exclude = tuple(used_accounts) if len(used_accounts) < want_distinct else ()
                        try:
                            borrowed = await self.access.pick(
                                plan["storage_chat"], backend=backend, need="write",
                                exclude=exclude,
                            )
                        except NoBackendAvailable:
                            borrowed = await self.access.pick(
                                plan["storage_chat"], backend=backend, need="write"
                            )
                        used_accounts.add(borrowed.key)
                    async with borrowed as be:

                        def part_progress(
                            done_bytes: int, _total: int, *, i=idx, key=borrowed.key,
                            enc=encoding, physical=stored_size,
                        ) -> None:
                            if enc == "gzip" and physical > 0:
                                done_bytes = int(int(done_bytes) * part_sizes[i] / physical)
                            done[i] = min(max(0, int(done_bytes)), part_sizes[i])
                            publish(key)

                        result = await be.send_document(
                            plan["storage_chat"], send_path, f"{name}.part{idx:04d}", mime,
                            caption=folder_tag, part_workers=part_workers,
                            progress_cb=part_progress,
                        )
                        mid = int(result["message_id"])
                        physical = int(result.get("size") or stored_size) or stored_size
                        message_ids[idx] = mid
                        await self.files.add_part(
                            file_id, idx, mid, part_len,
                            stored_size=physical, encoding=encoding,
                            account_key=borrowed.key,
                        )
                        await self.access.mark(
                            borrowed.key, plan["storage_chat"], backend,
                            can_read=True, can_write=True, source="upload",
                        )
                        done[idx] = part_sizes[idx]
                        publish(borrowed.key)
            finally:
                if compressed:
                    try:
                        os.remove(send_path)
                    except OSError:
                        pass

        tasks = [
            asyncio.create_task(send_part(idx, ppath, part_sizes[idx]))
            for idx, ppath in enumerate(part_paths)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            for ppath in part_paths:
                try:
                    os.remove(ppath)
                except OSError:
                    pass

        account_key = ",".join(sorted(used_accounts)) or "auto"
        self._last_backend_by_job[job_id] = account_key
        return account_key, message_ids, str(plan["storage_chat"])

    def _auto_split_free_rows(self, backend: str) -> List[Dict[str, Any]]:
        if backend == "eitaa":
            return []
        try:
            rows = self.manager.health()
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            key = str(row.get("key") or "")
            if not key.startswith("acc:") or not row.get("available"):
                continue
            try:
                free = int(row.get("upload_free") or 0)
            except (TypeError, ValueError):
                free = 0
            if free > 0:
                out.append(row)
        return out

    def _auto_split_free_accounts(self, backend: str) -> List[str]:
        return [str(row.get("key")) for row in self._auto_split_free_rows(backend)]

    async def _shared_storage_chat(self, explicit: str, keys: List[str]) -> str:
        """Resolve one destination chat that every auto-split account can share.

        Saved Messages (``me``) is per-account and must never be used for a
        multi-account file: the downloader could acquire a different account.
        """
        chat = (explicit or "").strip()
        if chat and chat.lower() not in ("me", "self"):
            return chat
        try:
            vals = await runtime_settings().get_all(self.db)
            chat = str(vals.get("tg_storage_chat") or "").strip()
        except Exception:
            chat = ""
        if chat and chat.lower() not in ("me", "self"):
            return chat
        chats: list[str] = []
        for key in keys:
            try:
                value = self.manager.storage_chat_of(key).strip()
            except Exception:
                value = ""
            chats.append(value)
        # Every account that may carry a part must be able to see the same
        # non-Saved destination.  Picking the one non-'me' chat while another
        # account defaults to Saved Messages would make that part unreachable
        # from the downloader's account.
        if not chats or not chats[0] or chats[0].lower() in ("me", "self"):
            return ""
        if any(c != chats[0] for c in chats[1:]):
            return ""
        return chats[0]

    async def _auto_split_plan(
        self, *, size: int, backend: str, split_at: int, storage_chat: str,
    ) -> Optional[Dict[str, Any]]:
        """Decide whether a file should use the optional multi-account path."""
        if size <= 0 or backend == "eitaa":
            return None
        s = get_settings()
        try:
            vals = await runtime_settings().get_all(self.db)
        except Exception:
            vals = {}

        def cfg(name: str, default: int) -> int:
            raw = vals.get(name, getattr(s, name, default))
            try:
                return int(raw)
            except (TypeError, ValueError):
                return int(default)

        if cfg("auto_split_enabled", 0) != 1:
            return None
        min_file = max(0, cfg("auto_split_min_file_size", 128 * 1024 * 1024))
        if size < min_file:
            return None
        min_part = max(1, cfg("auto_split_min_part_size", 32 * 1024 * 1024))
        max_part = max(1, cfg("auto_split_max_part_size", 512 * 1024 * 1024))
        # the original hard cap always wins over the auto-split preference
        max_part = min(max_part, max(1, int(split_at or max_part)))
        if max_part < min_part:
            return None
        min_accounts = max(2, cfg("auto_split_min_accounts", 2))
        max_accounts = max(2, cfg("auto_split_max_accounts", 4))
        max_parts = max(2, cfg("auto_split_max_parts", 64))
        free = self._auto_split_free_accounts(backend)
        if len(free) < min_accounts:
            return None
        target = await self._shared_storage_chat(storage_chat, free)
        if not target:
            return None

        # Only accounts that can write to the shared destination may carry a
        # part.  Cached positives are cheap; unknown accounts get one lazy
        # probe; an unprobeable backend keeps the legacy behaviour.
        authorized: List[str] = []
        for key in free:
            try:
                cached = await self.access.get(key, target, backend)
                if cached is not None and not int(cached.get("can_write") or 0):
                    continue
                if cached is not None:
                    authorized.append(key)
                    continue
                allowed = await self.access.probe(key, target, backend, "write")
                if allowed is not False:
                    authorized.append(key)
            except Exception:
                authorized.append(key)
        free = authorized
        if len(free) < min_accounts:
            return None
        accounts = min(len(free), max_accounts)
        if accounts < 2:
            return None
        parts = max(accounts, (size + max_part - 1) // max_part)
        max_by_min = size // min_part
        if max_by_min < 2:
            return None
        parts = min(parts, max_by_min, max_parts)
        if parts < 2:
            return None
        if (size + parts - 1) // parts > max_part:
            return None
        if size // parts < min_part:
            return None
        return {
            "parts": parts,
            "concurrency": min(accounts, parts),
            "storage_chat": target,
            "account_keys": free[:max_accounts],
            "min_accounts": min_accounts,
        }

    async def _maybe_store_thumb(self, rec: Dict[str, Any], tmp_path: str) -> None:
        """Best-effort: build a REAL thumbnail for this media type and keep it as a
        separate one-message doc so the public page can show it.

        The source file is never re-uploaded: images are scaled with Pillow and
        videos are sampled to a single frame with ffmpeg (see app/services/thumbs.py),
        which is what makes thumbnails cheap instead of doubling storage. Types we
        cannot decode (PDF) simply get no thumbnail — still cheaper than a copy.
        """
        mime = (rec.get("mime") or "").lower()
        if rec.get("backend") == "eitaa":
            return  # telegram-only feature
        from ..services.thumbs import THUMB_MAX_PX, THUMB_QUALITY, build_thumbnail

        # thumbnail geometry is operator-configurable (settings tab); runtime
        # value wins, then the env/default from config, then the module constant
        s = get_settings()
        try:
            max_px = int(await get_runtime(self.db, "thumb_max_px") or s.thumb_max_px or THUMB_MAX_PX)
        except Exception:
            max_px = int(s.thumb_max_px or THUMB_MAX_PX)
        try:
            quality = int(await get_runtime(self.db, "thumb_quality") or s.thumb_quality or THUMB_QUALITY)
        except Exception:
            quality = int(s.thumb_quality or THUMB_QUALITY)
        try:
            thumb = await asyncio.to_thread(
                build_thumbnail, tmp_path, mime, int(rec.get("size") or 0), max_px=max_px, quality=quality
            )
        except Exception as exc:
            slog_q.warning("thumbnail build failed", file_id=rec["id"], error=str(exc)[:120])
            return
        if thumb is None:
            return  # unsupported/undecodable → store nothing
        fd, thumb_path = tempfile.mkstemp(suffix=thumb.ext)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(thumb.data)
            borrowed = await self.manager.acquire("acc", direction="upload")
            async with borrowed as backend:
                storage_chat = getattr(backend, "storage_chat", "me")
                name = f"thumb_{rec['id']}{thumb.ext}"
                result = await backend.send_document(storage_chat, thumb_path, name, thumb.mime)
                await self.files.set_thumb(rec["id"], int(result["message_id"]), thumb.mime)
            slog_q.info(
                "thumbnail stored",
                file_id=rec["id"],
                thumb_bytes=thumb.size,
                source_bytes=int(rec.get("size") or 0),
                dims=f"{thumb.width}x{thumb.height}",
            )
        except Exception as exc:
            slog_q.warning("thumbnail store skipped", file_id=rec["id"], error=str(exc)[:120])
        finally:
            try:
                os.remove(thumb_path)
            except OSError:
                pass

    @staticmethod
    async def _split_file_balanced(tmp_path: str, parts: int) -> List[str]:
        """Split into exactly ``parts`` near-equal files without loading a part in RAM."""
        if parts < 1:
            raise ValueError("parts must be >= 1")

        def _work() -> List[str]:
            size = os.path.getsize(tmp_path)
            base, ext = os.path.splitext(tmp_path)
            part_paths: List[str] = []
            chunk_size = 1024 * 1024
            each, remainder = divmod(size, parts)
            fh_in = open(tmp_path, "rb")
            try:
                for idx in range(parts):
                    remaining = each + (1 if idx < remainder else 0)
                    if remaining <= 0:
                        continue
                    ppath = f"{base}.s{idx:04d}{ext}"
                    with open(ppath, "wb") as fh_out:
                        while remaining > 0:
                            chunk = fh_in.read(min(chunk_size, remaining))
                            if not chunk:
                                raise IOError(f"short read while splitting part {idx}")
                            fh_out.write(chunk)
                            remaining -= len(chunk)
                    part_paths.append(ppath)
            except BaseException:
                for ppath in part_paths:
                    try:
                        os.remove(ppath)
                    except OSError:
                        pass
                raise
            finally:
                fh_in.close()
            return part_paths

        return await asyncio.to_thread(_work)

    @staticmethod
    async def _split_file(tmp_path: str, part_size: int) -> List[str]:
        part_paths: List[str] = []

        def _work() -> List[str]:
            idx = 0
            out = tmp_path
            base, ext = os.path.splitext(out)
            fh_in = open(out, "rb")
            try:
                while True:
                    chunk = fh_in.read(part_size)
                    if not chunk:
                        break
                    ppath = f"{base}.p{idx:04d}{ext}"
                    with open(ppath, "wb") as fh_out:
                        fh_out.write(chunk)
                    part_paths.append(ppath)
                    idx += 1
            finally:
                fh_in.close()
            return part_paths

        return await asyncio.to_thread(_work)

    async def _send_streaming(
        self,
        be, storage_chat: str, tmp_path: str, name: str, mime: str, folder_tag: str,
        file_id: str, message_ids: List[int], from_offset: int,
    ) -> None:
        """Plain (non-split) upload that continues from `from_offset`.

        We stream the remaining bytes (from `from_offset`) to the backend so a
        resumed upload never re-sends what it already stored. On success the
        upload session's `offset` is advanced to the file `size` so the offset
        never rewinds.
        """
        size = int((await self.files.get(file_id))["size"])
        sent = 0
        try:
            with open(tmp_path, "rb") as fh:
                fh.seek(from_offset)
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    try:
                        result = await be.send_document(storage_chat, tmp_path, name, mime, caption=folder_tag)
                    except Exception as exc:
                        slog_q.warning(
                            "upload send_document failed (resumed)",
                            job_id=self._last_backend_by_job.get("", "?"),
                            error=str(exc)[:200],
                        )
                        raise
                    message_ids.append(int(result["message_id"]))
                    await self.files.add_part(file_id, 0, int(result["message_id"]), int(result["size"]))
                    sent += int(result["size"])
                    if sent >= size:
                        break
        finally:
            if from_offset and message_ids:
                try:
                    from ..core.models import UploadSessionRepo

                    await UploadSessionRepo(self.db).advance(file_id, size)
                except Exception:
                    pass
        slog_q.info(
            "upload resumed complete",
            job_id=self._last_backend_by_job.get("", "?"),
            file_id=file_id,
            bytes_sent=sent,
            size=size,
        )

    # ── download handler (bot delivery) ───────────────────────
    async def _handle_download(self, payload: Dict[str, Any]) -> None:
        file_id = payload["file_id"]
        rec = await self.files.get(file_id)
        if not rec:
            raise TransferError(f"file {file_id} missing")
        deliver = payload.get("deliver_to") or {}
        if not deliver.get("bot"):
            raise TransferError("download job without delivery target")

        job_id = str(payload.get("__job_id") or "")
        os.makedirs(get_settings().final_tmp_dir(), exist_ok=True)
        tmp = str(payload.get("downloaded_path") or f"{get_settings().final_tmp_dir()}/{file_id}.out")
        total = int(rec.get("size") or 0)
        account_key = ""
        if not (payload.get("downloaded_path") and os.path.exists(tmp)):
            parts = await self.files.parts(file_id)
            from ..services.streaming import IncompleteFileError, iter_stored_part, validate_parts

            try:
                parts = validate_parts(rec, parts)
            except IncompleteFileError as exc:
                raise TransferError(f"file incomplete: {exc}") from exc
            total = 0
            chat = rec["storage_chat"] or "me"
            preferred = next(
                (str(p.get("account_key") or "") for p in parts if p.get("account_key")), ""
            )
            borrowed = await self.access.pick(
                chat,
                backend=rec.get("backend") or "telegram",
                need="read",
                preferred=preferred,
            )
            async with borrowed as backend:
                account_key = borrowed.key
                self._last_backend_by_job[job_id] = borrowed.key

                with open(tmp, "wb") as fh:
                    for part in parts:
                        async for chunk in iter_stored_part(
                            backend, chat, part, start=0, end=None
                        ):
                            fh.write(chunk)
                            total += len(chunk)
            # Keep the download for delivery retries: a bot 429 must not force a
            # second multi-GB transfer from Telegram.
            payload["downloaded_path"] = tmp
            if job_id:
                try:
                    await self.jobs.update_payload(job_id, payload)
                except Exception:
                    pass
        try:
            await self.bot_service.deliver_download(deliver["bot"], deliver["chat_id"], tmp, rec["name"])
        except Exception:
            # leave tmp in place; _retry reuses downloaded_path
            raise
        try:
            os.remove(tmp)
        except OSError:
            pass
        await self.files.count_download(file_id, total)
        metrics.inc("download.bytes", total)
        slog_q.info("bot download complete", file_id=file_id, bytes=total, account=account_key)

    # ── delete handler ─────────────────────────────────────────
    async def _handle_delete(self, payload: Dict[str, Any]) -> None:
        file_id = payload["file_id"]
        rec = await self.files.get(file_id)
        if not rec:
            return
        parts = await self.files.parts(file_id)
        failed: List[Dict[str, Any]] = []
        if parts:
            chat = rec["storage_chat"] or "me"
            preferred = next(
                (str(p.get("account_key") or "") for p in parts if p.get("account_key")), ""
            )
            borrowed = await self.access.pick(
                chat,
                backend=rec.get("backend") or "telegram",
                need="read",
                preferred=preferred,
            )
            async with borrowed as backend:
                self._last_backend_by_job[payload.get("__job_id", "")] = borrowed.key
                for part in parts:
                    try:
                        await backend.delete_message(part["message_id"], chat)
                    except Exception as exc:
                        text = str(exc).lower()
                        if any(word in text for word in ("not found", "no such", "already deleted", "invalid message")):
                            continue
                        log.warning("delete part failed: %s", exc)
                        failed.append(part)
        if failed:
            # Keep the record so a retry can still find the remaining messages;
            # deleting rows now would make those Telegram messages unreachable.
            await self._record_orphans(file_id, rec, failed, "delete failed")
            raise TransferError(f"could not delete {len(failed)}/{len(parts)} parts")
        await self.files.delete(file_id)
        metrics.inc("queue.done.delete")
        slog_q.info("file deleted", file_id=file_id, parts=len(parts))

    # ── transfer handler (bulk channel move) ──────────────────
    async def _handle_transfer(self, payload: Dict[str, Any]) -> None:
        """Move a ready file to a different storage chat.

        Downloads each part from the old chat and re-sends it to the new one
        (send_document), then rewrites the file's part pointers. The old
        messages stay in the source chat as a safety net — janitor-style
        cleanup can be added later; a failed re-send retries with backoff and
        leaves the record untouched until success.
        """
        file_id = payload["file_id"]
        target_chat = (payload.get("target_chat") or "").strip()
        rec = await self.files.get(file_id)
        if not rec:
            return  # deleted meanwhile; nothing to move
        if not target_chat:
            raise TransferError("transfer job without target_chat")
        if rec["status"] != "ready":
            raise TransferError(f"file not ready (status={rec['status']})")
        src_chat = rec["storage_chat"]
        if src_chat and src_chat == target_chat:
            return  # already there
        parts = await self.files.parts(file_id)
        if not parts:
            raise TransferError("file has no parts to transfer")
        from ..services.streaming import IncompleteFileError, validate_parts

        try:
            parts = validate_parts(rec, parts)
        except IncompleteFileError as exc:
            raise TransferError(f"file incomplete: {exc}") from exc
        folder_path = ""
        try:
            if rec.get("folder_id"):
                folder_path = await FolderRepo(self.db).path_of(int(rec["folder_id"]))
        except Exception:
            folder_path = ""
        tags = " ".join("#" + p.strip().replace(" ", "_") for p in folder_path.split("/") if p.strip())

        preferred = next(
            (str(p.get("account_key") or "") for p in parts if p.get("account_key")), ""
        )
        src_borrowed = await self.access.pick(
            src_chat or "me",
            backend=rec.get("backend") or "telegram",
            need="read",
            preferred=preferred,
        )
        try:
            target_borrowed = await self.access.pick(
                target_chat,
                backend=rec.get("backend") or "telegram",
                need="write",
            )
        except Exception:
            # release the source slot before surfacing a target-access failure
            await src_borrowed.__aexit__(None, None, None)
            raise
        new_ids: List[int] = []
        job_id = payload.get("__job_id", "")
        await self._remember_tmp_paths(
            job_id,
            [
                f"{get_settings().final_tmp_dir()}/{file_id}.{part['idx']}.transfer"
                for part in parts
            ],
        )
        job_row = await self.jobs.fetch(job_id) if job_id else None
        # JobRepo.fetch returns the raw payload TEXT — parse it once here so
        # both the pre-loop and in-loop progress writes spread a dict, not a str.
        raw_payload = (job_row or {}).get("payload")
        if isinstance(raw_payload, str):
            try:
                cur_payload = json.loads(raw_payload or "{}")
            except Exception:
                cur_payload = {}
        else:
            cur_payload = raw_payload or {}
        total_bytes = sum(int(p.get("stored_size") or p.get("size") or 0) for p in parts)
        progress = {
            "file_id": file_id,
            "file_name": rec["name"],
            "target_chat": target_chat,
            "parts_total": len(parts),
            "bytes_total": total_bytes,
        }
        if job_row:
            await self.jobs.update_payload(job_id, {**cur_payload, "progress": progress, "progress_pct": 0})
        async with src_borrowed as src_backend, target_borrowed as target_backend:
            self._last_backend_by_job[payload.get("__job_id", "")] = target_borrowed.key
            src = src_chat or getattr(src_backend, "storage_chat", "me")
            done_bytes = 0
            last_pct = -1
            for part in parts:
                # one temp file per part; the whole source part is re-sent verbatim
                tmp = f"{get_settings().final_tmp_dir()}/{file_id}.{part['idx']}.transfer"
                try:
                    with open(tmp, "wb") as fh:
                        stored_size = int(part.get("stored_size") or part["size"])
                        async for chunk in src_backend.iter_file(
                            part["message_id"], src, start=0, end=None, size=stored_size
                        ):
                            fh.write(chunk)
                            if job_id:
                                done_bytes += len(chunk)
                                pct = int(done_bytes * 100 / total_bytes) if total_bytes else 0
                                if pct != last_pct:  # throttle DB writes to pct changes
                                    last_pct = pct
                                    await self.jobs.update_payload(
                                        job_id,
                                        {**cur_payload, "progress": {**progress, "bytes_done": done_bytes}, "progress_pct": pct},
                                    )
                    name = rec["name"] if len(parts) == 1 else f"{rec['name']}.part{part['idx']:04d}"
                    result = await target_backend.send_document(target_chat, tmp, name, rec["mime"], caption=tags)
                    new_ids.append(int(result["message_id"]))
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        await self.files.set_parts(
            file_id, new_ids, target_chat, account_key=target_borrowed.key
        )
        await self.access.mark(
            target_borrowed.key, target_chat, rec.get("backend") or "telegram",
            can_read=True, can_write=True, source="transfer",
        )
        metrics.inc("transfer.files")
        slog_q.info("file transferred", file_id=file_id, from_chat=src_chat, to_chat=target_chat, parts=len(new_ids))

        # optional source cleanup: the copies in the old chat are dead weight
        # once the record points at the target. Best-effort — a failed source
        # delete must NOT fail the (already successful) transfer job. Runs on
        # a freshly borrowed backend so the previous borrow is released first.
        try:
            delete_source = int(await get_runtime(self.db, "transfer_delete_source") or 0) == 1
        except Exception:
            delete_source = False
        if delete_source and src_chat and src_chat != target_chat:
            try:
                cleanup = await self.access.pick(
                    src_chat or "me",
                    backend=rec.get("backend") or "telegram",
                    need="read",
                )
            except Exception as exc:
                log.warning("transfer source-cleanup borrow failed: %s", exc)
                cleanup = None
            if cleanup is not None:
                async with cleanup as backend:
                    deleted = 0
                    for part in parts:
                        try:
                            await backend.delete_message(part["message_id"], src)
                            deleted += 1
                        except Exception as exc:
                            log.warning("transfer source delete failed (%s/%s): %s", src, part["message_id"], exc)
                    if deleted:
                        metrics.inc("transfer.source_deleted")
                        slog_q.info("transfer source cleaned", file_id=file_id, chat=src, parts=deleted)

    # ── introspection ──────────────────────────────────────────
    async def _stall_info(self) -> Dict[str, Any]:
        """Live stall info for the queue tab banner and dash cards."""
        waiting_rows = self._waiting_uploads()
        t = now()
        oldest = 0.0
        for r in waiting_rows:
            age = t - max(float(r.get("next_run_at") or 0), float(r.get("created_at") or 0))
            if age > oldest:
                oldest = age
        gw_age = self._gate_wait_age()
        if gw_age > oldest:
            oldest = gw_age
        try:
            threshold = int(await get_runtime(self.db, "upload_stall_threshold_s") or 0)
        except Exception:
            threshold = 0
        gate = self._upload_gate
        return {
            "stalled": self._stall_since is not None,
            "since": self._stall_since,
            "waiting": len(waiting_rows) + len(self._gate_waiters),
            "oldest_waiting_s": round(oldest, 1),
            "threshold": threshold,
            "gate_full": gate is not None and gate._value <= 0,
            "gate_capacity": self._upload_gate_val,
            "upload_workers": self._worker_counts.get("upload", 0),
        }

    def _flooded_backends(self) -> List[Dict[str, Any]]:
        """acc/eit backends currently flood-isolated (for the queue banner)."""
        t = time.time()
        return [
            {
                "key": k,
                "remaining_s": int(v - t),
                "burst_alerted": bool(self.manager._flood_alerts.get(k, {}).get("last_alert_at")),
            }
            for k, v in sorted(self.manager._flood_until.items())
            if k.startswith(("acc:", "eit:")) and v > t
        ]

    async def admit(self, size_bytes: int) -> Tuple[bool, int, str]:
        """Admission control before an upload is written to disk.

        Returns ``(ok, retry_after_s, reason)``. The DB count/sum is intentionally
        simple: the queue is bounded by max_queue_jobs in normal operation, and
        this check runs once per upload request, not per worker tick.
        """
        s = self.settings
        try:
            vals = await runtime_settings().get_all(self.db)
        except Exception:
            vals = {}
        max_jobs = int(vals.get("max_queue_jobs") or s.max_queue_jobs or 0)
        max_bytes = int(vals.get("max_inflight_bytes") or s.max_inflight_bytes or 0)
        pct = int(vals.get("disk_watermark_pct") or s.disk_watermark_pct or 90)
        active = "status IN ('pending','retry','running')"
        if max_jobs > 0:
            n = int(await self.db.scalar(f"SELECT COUNT(*) FROM jobs WHERE {active}") or 0)
            if n >= max_jobs:
                return False, 30, f"queue full ({n}/{max_jobs})"
        if max_bytes > 0:
            rows = await self.db.fetch_all(f"SELECT payload FROM jobs WHERE {active}")
            total = sum(job_size(r) for r in rows)
            if total + max(0, size_bytes) > max_bytes:
                return False, 30, f"in-flight bytes limit ({total}/{max_bytes})"
        if size_bytes >= 16 * 1024 * 1024:
            # Small writes cannot move a percentage watermark; rejecting a 2KB
            # file because the disk is at 91% is operationally pointless and
            # makes a healthy app look broken near the threshold. Large writes
            # still get hard backpressure.
            try:
                os.makedirs(s.final_tmp_dir(), exist_ok=True)
                usage = shutil.disk_usage(s.final_tmp_dir())
                if (usage.free - size_bytes) < usage.total * (pct / 100.0):
                    return False, 60, f"disk watermark ({pct}% free required)"
            except OSError:
                pass
        return True, 0, ""

    async def stats(self) -> Dict[str, Any]:
        base = await self.jobs.stats()
        base["paused"] = self.paused()
        base["heap_size"] = 0  # one DB-backed claim path; kept for panel compatibility
        base["stall"] = await self._stall_info()
        base["flooded"] = self._flooded_backends()
        t = now()
        small, max_wait, scan, _lease = await self._policy()
        pending = [r for r in self._rows.values() if r.get("status") in ("pending", "retry")]
        lanes: Dict[str, int] = {}
        for r in pending:
            lane = lane_for(r, small)
            lanes[lane] = lanes.get(lane, 0) + 1
        base["lanes"] = lanes
        base["oldest_pending_s"] = round(
            max([t - float(r.get("created_at") or t) for r in pending], default=0.0), 1
        )
        base["overdue"] = sum(1 for r in pending if is_overdue(r, now=t, max_wait_s=max_wait))
        base["workers_alive"] = len([w for w in self._workers if not w.done()])
        base["upload_pending"] = base["by_kind"].get(KIND_UPLOAD, {}).get("pending", 0)
        base["upload_running"] = base["by_kind"].get(KIND_UPLOAD, {}).get("running", 0)
        base["scheduler"] = {
            "small_max_bytes": small, "max_wait_s": max_wait, "scan_limit": scan,
        }
        return base
