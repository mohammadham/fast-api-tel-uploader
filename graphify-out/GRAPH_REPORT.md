# Graph Report - fast api tel uploader  (2026-09-22)

## Corpus Check
- Corpus is ~25,304 words - fits in a single context window. You may not need a graph.

## Summary
- 627 nodes · 1487 edges · 28 communities (18 shown, 10 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 137 edges (avg confidence: 0.96)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Auth & Accounts API
- Database & Models Core
- Observability & Logging
- File Repository Layer
- API Keys Management
- Queue Control API
- Admin & Metrics API
- Bot Token Management
- Streaming & Range Serving
- Rate Limiting & Quota
- TG Account Pool Manager
- Architecture Concepts
- Account CRUD Operations
- FastTelethon Transfer
- Web Panel SPA
- Hardening Test Suite
- Telegram Login Flow
- Test Fixtures & Crypto
- Settings & Config
- Prometheus Metrics
- Failure Auto-Disable
- Community 21
- Community 26

## God Nodes (most connected - your core abstractions)
1. `get_settings()` - 53 edges
2. `Database` - 47 edges
3. `TGManager` - 39 edges
4. `AccountRepo` - 37 edges
5. `FileRepo` - 35 edges
6. `QueueManager` - 34 edges
7. `now()` - 28 edges
8. `BotRepo` - 27 edges
9. `TransferError` - 21 edges
10. `BackendClient` - 19 edges

## Surprising Connections (you probably didn't know these)
- `لینک‌های Presigned` ----> `app/api/ (7 روتر)`  [INFERRED]
  PLAN.md → app/main.py
- `GRAPH.md — گراف معماری دستی` ----> `app/api/ (7 روتر)`  [INFERRED]
  GRAPH.md → app/main.py
- `دیزاین‌سیستم پنل (OLED)` ----> `static/ (پنل SPA)`  [INFERRED]
  DECISIONS.md → static/app.js
- `دور سخت‌سازی` ----> `app/api/ (7 روتر)`  [INFERRED]
  DECISIONS.md → app/main.py
- `static/ (پنل SPA)` ----> `app/api/ (7 روتر)`  [INFERRED]
  static/app.js → app/main.py

## Import Cycles
- None detected.

## Communities (28 total, 10 thin omitted)

### Community 0 - "Auth & Accounts API"
Cohesion: 0.06
Nodes (74): _fake_session(), _gc_logins(), login_start(), Telegram account management: phone-login flow + CRUD + health actions. The…, _safe_disconnect(), StartIn, login(), LoginIn (+66 more)

### Community 1 - "Database & Models Core"
Cohesion: 0.06
Nodes (43): abc, aiosqlite, Async SQLite access layer (WAL) with schema bootstrap. A single shared…, Minimal Prometheus-style metrics registry (no external dependency)., Job, Typed models + repository helpers shared across services., Persistent priority queue + workers. Download-first policy: jobs are served by…, Periodic janitor: stale tmp files, expired upload sessions, old finished jobs. (+35 more)

### Community 2 - "Observability & Logging"
Cohesion: 0.05
Nodes (42): ContextFilter, current_ids(), JsonFormatter, monotonic_ms(), new_request_id(), Any, Structured (JSON) logging + request/correlation id propagation. Every log line…, logger.info("msg", **fields) sugar — fields become JSON keys. (+34 more)

### Community 3 - "File Repository Layer"
Cohesion: 0.06
Nodes (9): FileRepo, JobRepo, now(), Any, UploadSessionRepo, Any, QueueManager, Block until a runnable job for this worker kind is available. (+1 more)

### Community 4 - "API Keys Management"
Cohesion: 0.07
Nodes (39): create_key(), KeyIn, KeyPatch, list_keys(), BaseModel, delete, get, patch (+31 more)

### Community 5 - "Queue Control API"
Cohesion: 0.07
Nodes (28): jobs(), pause(), PauseIn, purge(), BaseModel, get, post, Queue inspection & control: stats, jobs list, pause/resume kinds, purge. (+20 more)

### Community 6 - "Admin & Metrics API"
Cohesion: 0.08
Nodes (16): audit(), healthz(), overview(), prometheus(), get, Admin/dashboard endpoints: overview stats, audit log, health, metrics., readyz(), Database (+8 more)

### Community 7 - "Bot Token Management"
Cohesion: 0.12
Nodes (8): delete_bot(), list_bots(), delete, get, BotRepo, BotService, Any, Validate token via getMe, store encrypted, start polling.

### Community 8 - "Streaming & Range Serving"
Cohesion: 0.11
Nodes (22): _416(), _ascii_name(), file_response(), gen(), _iter_multipart(), _iter_single(), parse_range(), _quote() (+14 more)

### Community 9 - "Rate Limiting & Quota"
Cohesion: 0.11
Nodes (10): DailyQuota, Exception, QuotaExceeded, RateLimiter, Token-bucket rate limiting (per API key / per IP) and daily quota accounting., In-memory token buckets keyed by arbitrary string (api key id / ip). Buckets…, Tracks bytes transferred per subject per UTC day (in-memory + DB persists…, TokenBucket (+2 more)

### Community 10 - "TG Account Pool Manager"
Cohesion: 0.18
Nodes (4): Borrow a healthy backend with free slot; waits if all busy/flooded. Selection…, Borrow one specific backend (health probes, targeted ops)., TGManager, Semaphore

### Community 11 - "Architecture Concepts"
Cohesion: 0.13
Nodes (20): استخر اکانت‌ها (TGManager), پارت‌بندی خودکار 1.9GB, دیزاین‌سیستم پنل (OLED), FakeTelegram (حالت تست), FastTelethon (انتقال موازی), Observability (لاگ JSON + correlation), لینک‌های Presigned, صف اولویت‌دار دانلود-محور (+12 more)

### Community 12 - "Account CRUD Operations"
Cohesion: 0.11
Nodes (6): delete_account(), list_accounts(), delete, get, AccountRepo, Count actually-served bytes per backend (best-effort).

### Community 13 - "FastTelethon Transfer"
Cohesion: 0.16
Nodes (14): _align_down(), download_to_file(), _bump(), one(), Any, FastTelethon-style transfer helpers. Parallel segmented download: the byte…, Async byte stream of [start, end] inclusive (HTTP Range semantics). Offsets are…, Upload a local file to Telegram (pipelined parts). Returns InputFile. (+6 more)

### Community 14 - "Web Panel SPA"
Cohesion: 0.46
Nodes (14): api(), badge(), esc(), fmtBytes(), loadAccounts(), loadAudit(), loadBots(), loadDash() (+6 more)

### Community 15 - "Hardening Test Suite"
Cohesion: 0.15
Nodes (4): NoBackendAvailable, Exception, Hardening round tests: security headers, logout blacklist, audit trail,…, test_permanent_error_disables_account()

### Community 16 - "Telegram Login Flow"
Cohesion: 0.17
Nodes (12): AccountOut, CompleteIn, login_complete(), BaseModel, patch, post, Directly test THIS account's backend (not a random pool member)., Clear flood/circuit/error state and reconnect (admin action after fixing an… (+4 more)

### Community 17 - "Test Fixtures & Crypto"
Cohesion: 0.27
Nodes (9): encrypt_str(), fixture, tempfile, api_key(), client(), Shared fixtures: app with FakeTelegram backend + tmp SQLite database., # NOTE: after cache_clear, rebuild the same values into the fresh instance, token() (+1 more)

## Knowledge Gaps
- **2 isolated node(s):** `telegramdrive`, `MASTER.md — دیزاین‌سیستم`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 214 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_settings()` connect `Auth & Accounts API` to `Database & Models Core`, `Observability & Logging`, `File Repository Layer`, `API Keys Management`, `Queue Control API`, `Admin & Metrics API`, `Bot Token Management`, `TG Account Pool Manager`, `Test Fixtures & Crypto`, `Settings & Config`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `Database` connect `Admin & Metrics API` to `Auth & Accounts API`, `Database & Models Core`, `Observability & Logging`, `File Repository Layer`, `API Keys Management`, `Queue Control API`, `Bot Token Management`, `Streaming & Range Serving`, `TG Account Pool Manager`, `Account CRUD Operations`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `TGManager` connect `TG Account Pool Manager` to `Database & Models Core`, `Observability & Logging`, `File Repository Layer`, `Admin & Metrics API`, `Bot Token Management`, `Streaming & Range Serving`, `Account CRUD Operations`, `Failure Auto-Disable`, `Community 21`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `Database` (e.g. with `AccountRepo` and `ApiKeyRepo`) actually correct?**
  _`Database` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `TGManager` (e.g. with `lifespan()` and `QueueManager`) actually correct?**
  _`TGManager` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `AccountRepo` (e.g. with `delete_account()` and `list_accounts()`) actually correct?**
  _`AccountRepo` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `FileRepo` (e.g. with `delete_file()` and `download_content()`) actually correct?**
  _`FileRepo` has 12 INFERRED edges - model-reasoned connections that need verification._