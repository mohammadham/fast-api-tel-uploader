# Queue, scheduling and load-management upgrade plan

Status: **Phase 0 and Phase 1 implemented**; Phase 2/3 are designed but deliberately not
enabled. The code review that motivated this plan is in
[`QUEUE_SCHEDULING_REVIEW.md`](./QUEUE_SCHEDULING_REVIEW.md).

## 1. Goals

- Keep interactive downloads responsive while large uploads/transfers are running.
- Prefer small and old work without starving a large file forever.
- Give every Telegram account its own download and upload capacity; bots and Eitaa
  must never dilute account slots.
- Make waiting visible and controllable from the panel (pause/resume, retry, lanes,
  age, pressure warning).
- Protect correctness: no duplicate sends, no fake resume, no deleting the source of
  a queued upload, no silently ignored Bot API errors.
- Keep SQLite deployments working without Redis; Redis is an optional scaling layer,
  never a requirement for a single node.

## 2. Current architecture after this change

```
API / bot / UI
      │
      ├── admission: queue depth, in-flight bytes, disk watermark, upload size
      ▼
durable jobs table (SQLite | MySQL | PostgreSQL)
      │  atomic claim: status pending/retry + next_run_at <= now
      │  bounded candidate scan -> pure scheduler.pick_best()
      ▼
QueueManager workers (download / upload)
      │  global upload gate (max_concurrent_uploads)
      │  per-direction acquire: TGManager
      ▼
TGManager
      ├── SlotLimiter(acc:N, download)   ── independent cap
      ├── SlotLimiter(acc:N, upload)     ── independent cap
      ├── SlotLimiter(bot:N, delivery)   ── independent cap
      ├── flood isolation (FloodWait.seconds, no attempt burned)
      └── circuit breaker with half-open probe
```

The durable table is the source of truth. `_heap` remains only as a compatibility
attribute for older tooling/tests; the live path is one DB-backed claim for every
engine. There is no new size column: `scheduler.job_size()` reads the payload, so an
upgrade does not require a schema migration.

## 3. Phases

### Phase 0 — correctness and safety (implemented)

| Change | Why |
|---|---|
| `FloodWait` retry waits the full Telegram delay and does **not** consume a retry attempt | A 10-minute flood used to burn five attempts in seconds and fail a healthy upload |
| Bot delivery checks HTTP status, JSON `ok`, and `retry_after` | Bot API errors were previously treated as success |
| Download tmp files are preserved until retry succeeds; janitor protects paths referenced by pending/running jobs | A long transfer or queued upload could lose its source after one hour |
| Upload “resume” no longer pretends to send from a byte offset; full send is recorded honestly and the session offset advances | A fake resume could duplicate or corrupt stored files |
| Existing uploaded parts are detected before a retry; bookkeeping completes instead of re-sending | Idempotent retries after a crash |
| Digest is written **before** `status=ready` | Pollers no longer observe an empty `sha256` for a split upload |
| Circuit breaker has a half-open probe and exponential cooldown | One failure burst no longer permanently removes a healthy account |
| `NoBackendAvailable` distinguishes temporary saturation from a missing backend | A key targeting Eitaa with no Eitaa account now fails visibly instead of deferring forever |

### Phase 1 — size/age-aware scheduling and explicit capacity (implemented)

| Change | Why |
|---|---|
| `app/queue/scheduler.py`: overdue → kind band → small/bulk lane → size → seq | Small files do not sit behind a 10 GB head-of-line job; old jobs are promoted before they starve |
| DB-backed atomic claim with bounded scan (`scheduler_scan_limit`) | One path for SQLite/MySQL/PostgreSQL; two nodes cannot both win the same job |
| Lease fields (`job_lease_seconds`) and recovery of expired running jobs | A crashed worker no longer leaves a job running forever |
| One `SlotLimiter` per `(backend, direction)` | Upload and download cannot consume each other's slots; bots/Eitaa are absent from the account denominator |
| Weighted least-loaded account selection + rotation on ties | Premium accounts get proportionally more work; sequential requests spread across the pool |
| Per-bot delivery limiter (`bot_max_concurrent`, `bot_max_rpm`) | One bot cannot hammer Bot API or monopolise the upload lane |
| Admission before writing (`max_queue_jobs`, `max_inflight_bytes`, large-file disk watermark) | Reject overload at ingest with `429` + `Retry-After` instead of filling the disk |
| Queue stats expose lanes, oldest pending age, overdue count, worker liveness | The panel can show *why* work is waiting, not just a count |
| Runtime settings + panel validation for all scheduler/admission/bot controls | Operational tuning without editing `.env` or restarting |

### Phase 2 — Redis and multi-node fairness (next, not enabled)

Target: keep the durable table as the audit/recovery record, and use Redis only for
coordination. This avoids a second source of truth and keeps SQLite-only installs
working.

1. **Wake-up channel**: Redis pub/sub `queue:wake` after enqueue and after a release;
   workers keep a short DB poll fallback. No job payloads in pub/sub.
2. **Distributed leases**: `SET queue:lease:<job_id> <node> NX PX <lease_ms>` before the
   DB claim; renew every `lease/3`. A lost lease stops the handler at the next
   checkpoint. The DB `lease_owner/lease_until` remains the recovery record.
3. **Redis Streams option** (behind `redis_enabled`): `XADD` on enqueue,
   consumer group per node, `XACK` after DB completion. If Redis is unavailable, the
   enqueue path writes the DB row and continues; reconciliation imports unacked
   messages. Do not make Redis the only copy of a job.
4. **Distributed rate budgets**: token bucket in Redis for `account_max_rpm` and
   `bot_max_rpm`; the in-process limiter remains the fast path for single-node.
5. **DRR/AIMD** (deficit round robin for account weights, additive-increase /
   multiplicative-decrease for per-account concurrency): start with a conservative
   cap, decrease on `FloodWait`, increase only after a clean window. Keep the hard
   per-account ceiling from settings.
6. **Cross-node disk guard**: admission must use the node that will store the tmp
   file; never reject based on another node's disk.

Acceptance for Phase 2: kill a worker mid-upload, verify exactly one node resumes and
no duplicate part is recorded; stop Redis, verify enqueue still succeeds and the DB
drains; run two nodes with one account and confirm the account cap is global.

### Phase 3 — fair streaming and tenant isolation (later)

- Streaming downloads currently bypass the queue but do take a download slot; add a
  per-key concurrent-stream cap and fair queueing so one API key cannot occupy every
  account slot.
- Separate quotas for API keys and bot users; surface `429` with `Retry-After` on the
  streaming endpoint.
- Optional bandwidth shaping per account/tenant; do not shape below the minimum needed
  for a single large file to make progress.
- Backpressure for public links: bounded prefetch buffers, no unbounded background
  reads when the client is slow.

## 4. Runtime settings (panel → Settings → Scheduler / Queue)

| Key | Default | Effect |
|---|---:|---|
| `max_concurrent_uploads` | 2 | global upload gate |
| `max_concurrent_uploads_per_account` | 2 | per-account upload slots |
| `max_concurrent_downloads` | 4 | per-account download slots |
| `job_lease_seconds` | 1800 | running-job lease; expired jobs are recovered |
| `scheduler_small_max_bytes` | 64 MiB | small-lane boundary |
| `scheduler_max_wait_s` | 900 | overdue promotion threshold; `0` disables aging |
| `scheduler_scan_limit` | 200 | candidate rows examined per claim |
| `max_queue_jobs` | 1000 | active-job admission limit; `0` = unlimited |
| `max_inflight_bytes` | 0 | active payload bytes limit; `0` = unlimited |
| `disk_watermark_pct` | 90 | reject large writes that would cross this free-space floor |
| `tmp_retention_hours` | 24 | inactive tmp retention; active job files are always protected |
| `bot_max_concurrent` | 2 | simultaneous Bot API sends per bot |
| `bot_max_rpm` | 60 | per-bot request budget; `0` = off |
| `account_max_rpm` | 0 | per-account Telegram request budget; `0` = off |
| `premium_weight` | 2 | selection weight for premium accounts (1–10) |

Validation is server-side (`app/core/settings_service.py`) and mirrored in
`panel/src/App.vue`. The panel queue tab shows kind, lane, size, wait time, attempts,
error, retry, and upload resume; the pressure card shows the global gate and flooded
backends.

## 5. Invariants and acceptance checks

- A job is owned by at most one worker: claim is a conditional `UPDATE`; a lost race
  returns to the scan loop, never to a second handler.
- Every successful acquire has exactly one release in the same direction, including
  cancellation and exceptions (`Borrowed.__aexit__`).
- `FloodWait` never increments attempts; permanent configuration errors do.
- Admission never breaks an upload if the guard itself errors; it fails open.
- Janitor never deletes a path referenced by `pending`, `retry`, or `running`.
- `status=ready` implies the stored digest is already written (or the digest step
  failed and was logged, which is explicitly allowed).

Checks used for this change:

```bash
.venv/Scripts/python.exe -m py_compile app/tg/manager.py app/tg/bot_service.py \
  app/queue/queue_manager.py app/queue/scheduler.py app/services/janitor.py
timeout 300 .venv/Scripts/python.exe -m pytest -q
cd panel && npm run build
```

Load checks to run before enabling Phase 2:

- 1000 uploads of 1 KiB + 10 uploads of 1 GiB: verify small-lane latency and no
  large-file starvation beyond `scheduler_max_wait_s`.
- one account, 20 concurrent uploads: only `max_concurrent_uploads_per_account` are
  in flight; the rest wait without consuming retry attempts.
- two accounts, one flooded: the healthy account continues; the flooded key is skipped
  until `retry_after`.
- kill the process with a running job: after restart, exactly one claim resumes it.

## 6. Rollout and rollback

1. Deploy code with new settings at defaults; no schema migration is required.
2. Watch `queue.stats()`: `oldest_pending_s`, `overdue`, `lanes`, `workers_alive`,
   retry/failed counters, and flooded backends.
3. To roll back scheduling policy: set `scheduler_max_wait_s=0` and raise
   `scheduler_small_max_bytes` above normal file sizes (restores approximate FIFO).
4. To roll back admission: set `max_queue_jobs=0`, `max_inflight_bytes=0`, and
   `disk_watermark_pct` to a value that does not reject current writes.
5. Redis remains optional; `redis_enabled=0` must always leave a working DB queue.
6. Never roll back by deleting `jobs`/`file_parts` rows; they are the recovery record.

## 7. Known limitations

- The scheduler runs in-process per node. Cross-node global fairness and distributed
  leases are Phase 2; today two nodes share the DB claim but keep separate limiter
  counters.
- Disk admission checks only large writes (≥16 MiB); aggregate small-write pressure is
  covered by `max_queue_jobs` and `max_inflight_bytes`.
- Streaming fairness is Phase 3; streaming already takes a download slot but has no
  per-key cap.
- The panel queue table is a snapshot; it is not a real-time scheduler console.
- `tmp_retention_hours` is a retention floor for inactive files, not a guarantee that
  a file survives if an operator manually cleans the tmp directory.
