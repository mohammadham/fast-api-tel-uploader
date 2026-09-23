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
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis.asyncio as redis

from ..core.config import get_settings
from ..core.db import Database
from ..core.metrics import metrics
from ..core.obs import correlation_id as corr_var, job_id as job_var, slog
from ..core.models import (
    AccountRepo,
    BotRepo,
    FileRepo,
    Job,
    JobRepo,
    KIND_DELETE,
    KIND_DOWNLOAD,
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
    def __init__(self, db: Database, manager: TGManager, bot_service=None) -> None:
        self.db = db
        self.manager = manager
        self.bot_service = bot_service
        self.settings = get_settings()
        self.jobs = JobRepo(db)
        self.files = FileRepo(db)
        self._heap: List[Tuple[int, int, str]] = []
        self._rows: Dict[str, Dict[str, Any]] = {}  # job_id → latest row snapshot
        self._wake = asyncio.Event()
        self._paused_kinds: set[str] = set()
        self._workers: List[asyncio.Task] = []
        self._stopped = False
        self._redis: Optional[redis.Redis] = None
        self._redis_enabled = self.settings.redis_enabled and self.settings.redis_url
        # Redis connection is initialized lazily on first use to avoid blocking startup
        self._redis_ping_done = False

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
        s = get_settings()
        for i in range(max(1, s.download_workers)):
            self._workers.append(asyncio.create_task(self._worker_loop("download", f"dl{i}")))
        for i in range(max(1, s.upload_workers)):
            self._workers.append(asyncio.create_task(self._worker_loop("upload", f"ul{i}")))
        log.info("queue started with %s workers", len(self._workers))

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()

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
        await self.jobs.insert(job)
        self._rows[job.id] = {
            "id": job.id, "kind": kind, "priority": priority, "seq": seq, "correlation_id": corr,
            "status": "pending", "payload": json.dumps(payload), "attempts": 0,
            "max_retries": job.max_retries, "next_run_at": 0,
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

    # ── worker loops ───────────────────────────────────────────
    async def _worker_loop(self, worker_kind: str, name: str) -> None:
        while not self._stopped:
            row = await self._next_job(worker_kind)
            if row is None:
                return
            await self._process(row, name)

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
                if worker_kind == "download" and row["kind"] == KIND_UPLOAD and self._downloads_waiting():
                    deferred.append(entry)
                    continue
                chosen = row
                break
            for entry in deferred:
                heapq.heappush(self._heap, entry)
            if chosen is not None:
                await self.jobs.update_fields(
                    chosen["id"], status="running", lease_owner=worker_kind[:2] + "?", lease_until=now() + 1800
                )
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
                    await self._handle_download(payload)
                elif kind == KIND_UPLOAD:
                    await self._handle_upload(payload, job_id)
                elif kind == KIND_DELETE:
                    await self._handle_delete(payload)
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
        backend = payload.get("backend") or rec.get("backend") or "telegram"

        message_ids: List[int] = []
        storage_chat = ""
        borrowed = await self.manager.acquire("acc", backend=backend)
        async with borrowed as be:
            account_key = borrowed.key
            storage_chat = rec["storage_chat"] or getattr(be, "storage_chat", "me") or ""
            if size > s.split_threshold and backend != "eitaa":
                # split into parts below the MTProto 2GB cap
                part_size = s.split_threshold
                part_paths = await self._split_file(tmp_path, part_size)
                try:
                    for idx, ppath in enumerate(part_paths):
                        part_name = f"{name}.part{idx:04d}"
                        result = await be.send_document(storage_chat, ppath, part_name, mime)
                        message_ids.append(int(result["message_id"]))
                        await self.files.add_part(file_id, idx, int(result["message_id"]), int(result["size"]))
                finally:
                    for ppath in part_paths:
                        try:
                            os.remove(ppath)
                        except OSError:
                            pass
            else:
                result = await be.send_document(storage_chat, tmp_path, name, mime)
                message_ids.append(int(result["message_id"]))
                await self.files.add_part(file_id, 0, int(result["message_id"]), int(result["size"]))

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
            async with await self.manager.acquire("acc", backend=rec.get("backend") or "telegram") as backend:
                chat = rec["storage_chat"] or getattr(backend, "storage_chat", "me")
                for part in parts:
                    try:
                        await backend.delete_message(part["message_id"], chat)
                    except Exception as exc:
                        log.warning("delete part failed: %s", exc)
        await self.files.delete(file_id)
        metrics.inc("queue.done.delete")
        slog_q.info("file deleted", file_id=file_id, parts=len(parts))

    # ── introspection ──────────────────────────────────────────
    async def stats(self) -> Dict[str, Any]:
        base = await self.jobs.stats()
        base["paused"] = self.paused()
        base["heap_size"] = len(self._heap)
        return base
