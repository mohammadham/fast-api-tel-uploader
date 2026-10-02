"""Persistent priority queue + workers.

Download-first policy: jobs are served by (-priority, seq). Download jobs
(prio 100) always precede uploads (40). Upload workers never pick downloads,
and download workers pause uploads while any runnable download waits.
Jobs persist in SQLite (WAL) so restarts resume pending work. FloodWait
isolates the backend (TGManager) and the job retries with exponential backoff.
"""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import logging
import os
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis.asyncio as redis

from ..core.config import get_settings
from ..core.db import Database
from ..core.metrics import metrics
from ..core.obs import correlation_id as corr_var, job_id as job_var, slog
from ..core.settings_service import get_runtime
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
from ..tg.base import FloodWait, SendFailure, TransferError
from ..tg.manager import TGManager

log = logging.getLogger("tgdrive.queue")
slog_q = slog("tgdrive.queue")

RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 60.0


class QueueManager:
    def __init__(self, db: Database, manager: TGManager, bot_service=None, node_id: str = "") -> None:
        self.db = db
        self.manager = manager
        self.bot_service = bot_service
        self.settings = get_settings()
        self.node_id = node_id or os.environ.get("TGDRIVE_NODE_ID") or socket.gethostname()
        self.jobs = JobRepo(db)
        self.files = FileRepo(db)
        self._heap: List[Tuple[int, int, str]] = []
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
        self._redis: Optional[redis.Redis] = None
        self._redis_enabled = self.settings.redis_enabled and self.settings.redis_url
        # Redis connection is initialized lazily on first use to avoid blocking startup
        self._redis_ping_done = False
        self._lease_task: Optional[asyncio.Task] = None
        self._hb_task: Optional[asyncio.Task] = None
        self._stall_task: Optional[asyncio.Task] = None

    async def _ensure_redis(self) -> None:
        """Initialize Redis connection if not yet done."""
        if self._redis_ping_done or not self._redis_enabled:
            return
        try:
            if self._redis is None:
                self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
            await self._redis.ping()
            self._redis_ping_done = True
            log.info("Redis queue backend enabled at %s", self.settings.redis_url)
        except Exception as e:
            log.warning("Redis unavailable (%s), falling back to SQLite only", e)
            self._redis = None
            self._redis_enabled = False
            self._redis_ping_done = True

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
        self._lease_task = asyncio.create_task(self._lease_loop())
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        self._stall_task = asyncio.create_task(self._stall_loop())
        log.info("queue started (node=%s) with %s workers", self.node_id, len(self._workers))

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        bg = (self._lease_task, self._hb_task, self._stall_task)
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

    # ── lease renewal + node heartbeat (multi-server) ─────────
    async def _lease_loop(self) -> None:
        """Renew leases of running jobs so other nodes don't steal them."""
        while not self._stopped:
            try:
                running = [jid for jid, r in self._rows.items() if r.get("status") == "running"]
                if running:
                    await self.jobs.renew_leases(running, self.node_id, 1800)
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
                await self.db.execute(
                    "INSERT INTO nodes(node_id, hostname, version, started_at, last_heartbeat, workers_dl, workers_ul)"
                    " VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(node_id) DO UPDATE SET hostname=excluded.hostname, version=excluded.version,"
                    " last_heartbeat=excluded.last_heartbeat, workers_dl=excluded.workers_dl, workers_ul=excluded.workers_ul",
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
        # Recover from Redis if enabled, otherwise SQLite
        if self._redis_enabled:
            try:
                job_ids = await self._redis.smembers("queue:ids")
                for job_id in job_ids:
                    data = await self._redis.hgetall(f"queue:job:{job_id}")
                    if not data:
                        continue
                    status = data.get("status", "pending")
                    self._rows[job_id] = {
                        "id": data.get("id", job_id),
                        "kind": data.get("kind"),
                        "priority": int(data.get("priority", 0)),
                        "seq": int(data.get("seq", 0)),
                        "correlation_id": data.get("correlation_id", ""),
                        "status": status,
                        "payload": data.get("payload", "{}"),
                        "attempts": int(data.get("attempts", 0)),
                        "max_retries": int(data.get("max_retries", 5)),
                    }
                    heapq.heappush(self._heap, (-self._rows[job_id]["priority"], self._rows[job_id]["seq"], job_id))
                log.info("queue recovered %s pending jobs from Redis", len(job_ids))
            except Exception as e:
                log.warning("Redis recovery failed, falling back to SQLite: %s", e)

        # Always also recover from SQLite as primary source
        rows = await self.db.fetch_all(
            "SELECT * FROM jobs WHERE status IN ('pending','retry','running','lease') ORDER BY priority DESC, seq"
        )
        for row in rows:
            status = "pending" if row["status"] in ("running", "lease") else row["status"]
            self._rows[row["id"]] = {**row, "status": status}
            heapq.heappush(self._heap, (-int(row["priority"]), int(row["seq"]), row["id"]))
            if status != row["status"]:
                await self.jobs.update_fields(row["id"], status=status, lease_owner="")
        log.info("queue recovered %s pending jobs (SQLite primary)", len(rows))

    # ── enqueue / control ──────────────────────────────────────
    async def enqueue(self, kind: str, payload: Dict[str, Any], priority: int, max_retries: Optional[int] = None) -> str:
        seq = int(time.time() * 1000)  # ms seq keeps FIFO within same priority
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
        }
        heapq.heappush(self._heap, (-priority, seq, job.id))
        self._wake.set()
        metrics.inc(f"queue.enqueued.{kind}")
        # Also store in Redis for scaling if enabled (lazy init)
        if self._redis_enabled:
            try:
                await self._ensure_redis()
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
        self._heap = [r for r in self._heap if r[2] != job_id]
        heapq.heapify(self._heap)

    # ── worker loops ───────────────────────────────────────────
    async def _worker_loop(self, worker_kind: str, name: str) -> None:
        while not self._stopped:
            if name.startswith("stop-"):
                return  # worker retired by set_worker_counts
            if not self.db.is_sqlite:
                row = await self._next_job_pg(worker_kind)
            else:
                row = await self._next_job(worker_kind)
            if row is None:
                return
            await self._process(row, name)

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
            if worker_kind == "upload":
                # uploads + transfers share the upload worker pool (same
                # telegram pressure profile)
                conds.append("kind IN (?, ?)")
                params.extend([KIND_UPLOAD, KIND_TRANSFER])
            else:
                conds.append("kind != ?")
                params.append("__none__")
            if self._paused_kinds:
                ph = ",".join("?" for _ in self._paused_kinds)
                conds.append(f"kind NOT IN ({ph})")
                params.extend(sorted(self._paused_kinds))
            if prefix == "ul":
                conds.append("(origin_node = ? OR origin_node = '')")
                params.append(self.node_id)
            sql = (
                "UPDATE jobs SET status='running', lease_owner=?, lease_until=?, updated_at=?"
                " WHERE id = (SELECT id FROM jobs WHERE " + " OR ".join(conds) +
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
                await self._retry(job_id, row, f"flood wait {exc.seconds}s", delay=min(exc.seconds, RETRY_MAX_DELAY))
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
            # handlers re-set this on every attempt; dropping it here keeps the
            # map bounded when jobs fail permanently
            self._last_backend_by_job.pop(job_id, None)
            job_var.reset(token)
            corr_var.reset(corr_token)

    async def _retry(self, job_id: str, row: Dict[str, Any], error: str, delay: Optional[float] = None) -> None:
        attempts = int(row.get("attempts", 0)) + 1
        kind = row["kind"]
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else {}
        if attempts > int(row.get("max_retries", 5)):
            await self.jobs.update_fields(job_id, status="failed", error=error[:500], finished_at=now(), attempts=attempts)
            if kind == KIND_UPLOAD and payload.get("file_id"):
                await self.files.set_status(payload["file_id"], "failed", error)
            self._rows.pop(job_id, None)
            metrics.inc(f"queue.failed.{kind}")
            self._wake.set()
            from ..services.notify import notify_admins

            try:
                await notify_admins(f"⛔ جاب شکست خورد: {kind}\n{error[:200]}")
            except Exception:
                pass
            return
        d = RETRY_BASE_DELAY * (2 ** (attempts - 1)) if delay is None else max(0.5, delay)
        d = min(d, RETRY_MAX_DELAY)
        await self.jobs.update_fields(
            job_id, status="retry", attempts=attempts, error=error[:500], next_run_at=now() + d, lease_owner=""
        )
        refreshed = await self.jobs.fetch(job_id)
        if refreshed:
            self._rows[job_id] = refreshed
            heapq.heappush(self._heap, (-int(refreshed["priority"]), int(refreshed["seq"]), job_id))
        metrics.inc("queue.retries")
        self._wake.set()

    # ── upload handler ─────────────────────────────────────────
    async def _handle_upload(self, payload: Dict[str, Any], job_id: str) -> None:
        from ..core.security import decrypt_str

        file_id = payload["file_id"]
        rec = await self.files.get(file_id)
        if not rec:
            raise TransferError(f"file {file_id} missing")
        tmp_path = payload.get("tmp_path", "")
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
            borrowed = await self.manager.acquire("acc", backend=backend)
            async with borrowed as be:
                account_key = borrowed.key
                self._last_backend_by_job[job_id] = borrowed.key
                # fall back to the backend default when neither the key nor the
                # record pins a chat ('' → send_document resolves 'me'/global)
                storage_chat = storage_chat or getattr(be, "storage_chat", "me") or ""
                if size > split_at and backend != "eitaa":
                    # split into parts below the MTProto 2GB cap
                    part_size = split_at
                    part_paths = await self._split_file(tmp_path, part_size)
                    try:
                        for idx, ppath in enumerate(part_paths):
                            part_name = f"{name}.part{idx:04d}"
                            result = await be.send_document(storage_chat, ppath, part_name, mime, caption=folder_tag)
                            message_ids.append(int(result["message_id"]))
                            await self.files.add_part(file_id, idx, int(result["message_id"]), int(result["size"]))
                    finally:
                        for ppath in part_paths:
                            try:
                                os.remove(ppath)
                            except OSError:
                                pass
                else:
                    # plain upload: stream from tmp_path at resumed_offset (0 = fresh)
                    if resumed_offset:
                        with open(tmp_path, "rb") as fh:
                            fh.seek(resumed_offset)
                            await self._send_streaming(be, storage_chat, tmp_path, name, mime, folder_tag, fh, file_id, message_ids, resumed_offset)
                    else:
                        result = await be.send_document(storage_chat, tmp_path, name, mime, caption=folder_tag)
                        message_ids.append(int(result["message_id"]))
                        await self.files.add_part(file_id, 0, int(result["message_id"]), int(result["size"]))
        finally:
            gate.release()

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

    async def _maybe_store_thumb(self, rec: Dict[str, Any], tmp_path: str) -> None:
        """Best-effort: if telegram derives a thumbnail for this media type, keep it
        as a separate one-message doc so the public page can show it."""
        import struct

        mime = (rec.get("mime") or "").lower()
        if not (mime.startswith("image/") or mime.startswith("video/") or mime == "application/pdf"):
            return
        if rec.get("backend") == "eitaa":
            return  # telegram-only feature
        try:
            # only bother for files telegram actually thumbnails (jpeg/png/webp source)
            if mime.startswith("image/") and not mime.startswith(("image/jpeg", "image/png", "image/webp")):
                return
            borrowed = await self.manager.acquire("acc")
            async with borrowed as backend:
                storage_chat = getattr(backend, "storage_chat", "me")
                name = f"thumb_{rec['id']}"
                result = await backend.send_document(storage_chat, tmp_path, name, mime)
                await self.files.set_thumb(rec["id"], int(result["message_id"]))
        except Exception as exc:
            slog_q.warning("thumbnail store skipped", file_id=rec["id"], error=str(exc)[:120])

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

        os.makedirs(get_settings().final_tmp_dir(), exist_ok=True)
        tmp = f"{get_settings().final_tmp_dir()}/{file_id}.out"
        parts = await self.files.parts(file_id)
        total = 0
        borrowed = await self.manager.acquire("acc", backend=rec.get("backend") or "telegram")
        async with borrowed as backend:
            account_key = borrowed.key
            self._last_backend_by_job[payload.get("__job_id", "")] = borrowed.key
            chat = rec["storage_chat"] or getattr(backend, "storage_chat", "me")
            with open(tmp, "wb") as fh:
                for part in parts:
                    async for chunk in backend.iter_file(
                        part["message_id"], chat, start=0, end=None, size=part["size"]
                    ):
                        fh.write(chunk)
                        total += len(chunk)
        try:
            await self.bot_service.deliver_download(deliver["bot"], deliver["chat_id"], tmp, rec["name"])
        finally:
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
        if parts:
            borrowed = await self.manager.acquire("acc", backend=rec.get("backend") or "telegram")
            async with borrowed as backend:
                self._last_backend_by_job[payload.get("__job_id", "")] = borrowed.key
                chat = rec["storage_chat"] or getattr(backend, "storage_chat", "me")
                for part in parts:
                    try:
                        await backend.delete_message(part["message_id"], chat)
                    except Exception as exc:
                        log.warning("delete part failed: %s", exc)
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
        folder_path = ""
        try:
            if rec.get("folder_id"):
                folder_path = await FolderRepo(self.db).path_of(int(rec["folder_id"]))
        except Exception:
            folder_path = ""
        tags = " ".join("#" + p.strip().replace(" ", "_") for p in folder_path.split("/") if p.strip())

        borrowed = await self.manager.acquire("acc", backend=rec.get("backend") or "telegram")
        new_ids: List[int] = []
        job_id = payload.get("__job_id", "")
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
        total_bytes = sum(int(p.get("size") or 0) for p in parts)
        progress = {
            "file_id": file_id,
            "file_name": rec["name"],
            "target_chat": target_chat,
            "parts_total": len(parts),
            "bytes_total": total_bytes,
        }
        if job_row:
            await self.jobs.update_payload(job_id, {**cur_payload, "progress": progress, "progress_pct": 0})
        async with borrowed as backend:
            self._last_backend_by_job[payload.get("__job_id", "")] = borrowed.key
            src = src_chat or getattr(backend, "storage_chat", "me")
            done_bytes = 0
            last_pct = -1
            for part in parts:
                # one temp file per part; the whole source part is re-sent verbatim
                tmp = f"{get_settings().final_tmp_dir()}/{file_id}.{part['idx']}.transfer"
                try:
                    with open(tmp, "wb") as fh:
                        async for chunk in backend.iter_file(
                            part["message_id"], src, start=0, end=None, size=part["size"]
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
                    result = await backend.send_document(target_chat, tmp, name, rec["mime"], caption=tags)
                    new_ids.append(int(result["message_id"]))
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        await self.files.set_parts(file_id, new_ids, target_chat)
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
                cleanup = await self.manager.acquire("acc", backend=rec.get("backend") or "telegram")
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

    async def stats(self) -> Dict[str, Any]:
        base = await self.jobs.stats()
        base["paused"] = self.paused()
        base["heap_size"] = len(self._heap)
        # admin-visibility extras: upload-queue stall + flooded backends
        base["stall"] = await self._stall_info()
        base["flooded"] = self._flooded_backends()
        return base
