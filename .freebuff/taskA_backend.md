# Task A (backend) — TelegramDrive

You are working in the repo at the CURRENT working directory (FastAPI + SQLite + Telethon telegram-drive project).

Run tests with: `.venv/Scripts/python.exe -m pytest tests/ -q` (all 111 currently pass — they MUST keep passing; run the relevant test file after each change, full suite at the end).

Scope: ONLY Python backend files (`app/**`, `tests/**`). Do NOT touch `panel/**`, `static/**`, `e2e/**`, `data-*/**`. Do NOT git commit, do NOT change `.env` / secrets.

Work strictly in this order. If a step is blocked, skip it, continue with the rest, and report why in the final summary.

## A1 (bug, highest priority) — bot replies are silently swallowed
`app/tg/bot_service.py::_reply` calls `self._http.post("/bot{token}/sendMessage", ...)` on an `httpx.AsyncClient` created WITHOUT `base_url`, so the relative URL raises MissingSchema which the bare `except: pass` hides. Every bot reply (help, status, errors, upload ack) is never sent.
- Build the absolute URL with the `base` variable that already exists in the calling methods (same pattern as the getUpdates/getMe calls: `f"{base}/bot{token}/sendMessage"`). Pass `base` into `_reply` (or store it at client creation).
- Replace `except: pass` with a logged warning (`log.warning("bot reply failed: %s", exc)`).
- Check `app/services/notify.py` has no same-class bug (it builds absolute URLs already — just confirm).

## A2 (bug) — recursive folder listing returns the whole table
In `app/api/folders.py::_list_folder_files`, when `include_subfolders=True` the code passes `folder_id=None` into `FileRepo.list(...)`, which removes the folder filter entirely — the endpoint `GET /api/v1/folders/{id}/all` then returns ALL undeleted files of the whole system, while `total` is computed from a folder-scoped CTE (inconsistent pagination).
- Always pass the real `folder_id` to `FileRepo.list` (its recursive CTE branch already handles include_subfolders correctly).
- Add/adjust a pytest in `tests/test_folders.py`: create folder A with subfolder A2, put files in A, A2 AND root; call `/api/v1/folders/{id}/all` and assert it returns exactly the A+A2 files (not root's), and that `total` matches the items count. Also assert `q` filtering works on the recursive endpoint.

## A3 (concurrency) — atomic job claim in the queue
`app/queue/queue_manager.py` claims jobs with select-then-update (`_next_job` reads a candidate row, then updates it). Two workers/nodes can grab the same job.
- Make the claim atomic: an `UPDATE jobs SET status='running', lease_owner=?, lease_until=? WHERE id=(SELECT id FROM jobs WHERE ...ready-to-run filter... ORDER BY priority DESC, seq LIMIT 1) AND status IN ('pending','retry')` + then SELECT the claimed row. With SQLite this is safe inside one write txn; keep the PG branch (`_next_job_pg`) consistent — use `FOR UPDATE SKIP LOCKED` there if it doesn't already.
- Keep the recovery logic in `_recover()` (running/lease → pending on boot) unchanged in behavior.
- Existing queue tests in `tests/test_queue.py` must still pass; add one concurrency sanity test if practical (two sequential claims must never return the same job id).

## A4 (tests) — cover the untested new endpoints
Add pytest coverage (follow the style of `tests/test_api.py` / `tests/test_folders.py`, using the existing fixtures):
- batch move `POST /api/v1/folders/{id}/files` (multi file_ids, invalid target, cycle case)
- `GET /api/v1/folders/{id}/files?q=...` search inside a folder
- `GET /api/v1/admin/blocked-files-report` (block a file via API, then expect it in the report)
- (A2's recursive test also belongs here)

## A5 (security) — client IP trust
`app/api/deps.py::get_client_ip` unconditionally trusts `X-Forwarded-For` (spoofable; it feeds rate limiting + audit).
- Add a `trusted_proxies: str = ""` setting to `app/core/config.py` (comma-separated IPs/CIDRs; empty = trust nobody).
- `get_client_ip` should only use XFF when `request.client.host` is in the trusted list; otherwise return the direct peer. Simple `ipaddress` parsing is fine, no new dependency.
- Add a small test: direct peer (untrusted) → XFF ignored; peer in trusted list → XFF used.

## A6 (perf) — hot-path indexes
In `app/core/db.py` add to BOTH the SQLite `SCHEMA` and the Postgres `SCHEMA_PG` (and to the lightweight ALTER-style migration path if that's how other late indexes were added — check how existing indexes are declared):
- `CREATE INDEX IF NOT EXISTS idx_files_storage_chat_created ON files(storage_chat, created_at DESC);`
- `CREATE INDEX IF NOT EXISTS idx_files_folder_created ON files(folder_id, created_at DESC);`
- `CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);`
- `CREATE INDEX IF NOT EXISTS idx_jobs_status_nextrun ON jobs(status, next_run_at);`
Keep naming consistent with existing idx_* names. Run full pytest after.

## A7 (perf) — cache storage-channels summary + keyset pagination
- `app/api/admin.py` `GET /admin/storage-channels` does 3 full aggregate scans over `files` on every call and the panel calls it often. Add a small module-level TTL cache (30s, invalidated by any file insert/delete/update if cheap to do; a plain time-based TTL is acceptable). Report cache hit/miss via existing `app/core/metrics.py` counters (`metrics.inc("admin.storage_channels.cache_hit")` etc.).
- In `app/core/models.py` `FileRepo.list/count`: keyset pagination is NOT required to replace offset — instead ensure `count()` uses the same WHERE set as `list()` (it already does) and add a `total` fast path when `offset=0, limit>=count` if trivially doable. If keyset is easy (WHERE (created_at,id) < (?,?) with order params), you may add it as `order="keyset-<field>"` support WITHOUT breaking existing callers. Skip if it risks regressions — the tests must stay green.

## A8 (security, only if A1–A7 done and green) — short-lived preview tokens
`GET /api/v1/files/{id}/preview?token=<full admin JWT>` puts a long-lived admin JWT into URLs (leaks via logs/Referer).
- Add an opaque short-lived preview token: table-free is fine — HMAC-signed `{file_id, exp}` (exp ≤ 10 min) via `app/core/security.py` helpers (`sign_preview_token` / `verify_preview_token`, constant-time compare).
- New endpoint `POST /api/v1/files/{id}/preview-token` (auth: admin JWT or API key) returns `{"token": "...", "expires_in": 600}`.
- `preview_file` accepts EITHER the panel JWT (as today) or a valid preview token in `?token=`.
- Update panel usage is OUT OF SCOPE (another agent does it) — but write the endpoint so `?token=<preview_token>` alone is sufficient for preview access.
- Tests: expired/garbage token rejected, valid token grants preview without any Authorization header.

## Reporting
End with a compact report: per-step status (done/skipped/why), files changed, pytest result (must be 111+ passed, 0 failed).
