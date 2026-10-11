# TelegramDrive API Reference

Self-contained reference for this system. Intended to be handed to an AI agent so it can call the API correctly.

## Epoch / baseline

- Base URL: `http://<host>:8000`
- API prefix: `/api/v1`
- Response body for errors: `{"detail": "..."}` with the HTTP status code.
- Two auth systems: **panel JWT** (`/api/v1/auth`) and **API keys** (`/api/v1/keys`).
- Every response carries `X-Request-ID` and `X-Correlation-ID` (echoes incoming ones). Logs are structured `tgdrive.*`.
- Big binary paths skip access logging: `/d/...` and `/api/v1/files/...`.

---

## 1. Auth

### 1.1 Panel JWT

**`POST /api/v1/auth/login`**

Body JSON:

```json
{"username": "admin", "password": "admin123"}
```

Success `200`:

```json
{"access_token": "<jwt>", "token_type": "bearer", "refresh_token": "<rt>"}
```

Use: `Authorization: Bearer <access_token>`.

**`POST /api/v1/auth/refresh`**

Body JSON `{"refresh_token": "<rt>"}`. Returns new `access_token` (+ new `refresh_token`). Old refresh token is invalidated.

**`POST /api/v1/auth/logout`**

Invalidates the current refresh token. Success `200 {"status":"ok"}`.

**`GET /api/v1/auth/me`**

Admin JWT required. Returns current admin identity.

### 1.2 API keys

**`POST /api/v1/keys`** — admin only.

Body JSON:

```json
{
  "name": "my-app",
  "scopes": "read,write",
  "rpm": 600,
  "daily_quota_gb": 10,
  "expires_in_days": 0,
  "backend": "",
  "storage_chat": ""
}
```

- `scopes`: `read`, `write`, `admin` (comma-separated). Invalid scope → `400`.
- `rpm`: requests/minute (default from `default_key_rpm`).
- `daily_quota_gb`: daily bytes cap (0 = unlimited; stored as `daily_quota_bytes`).
- `backend`: `""` = follow system default, `"telegram"`, `"eitaa"`.
- `storage_chat`: `@username` or numeric `-100...` id, or `""` = system default per-key storage channel.

Success `200` returns the key object **including `"key":"<raw>"` — shown exactly once**. Store it.

Authenticate with:

- `Authorization: Bearer <raw_key>`
- `X-Api-Key: <raw_key>`
- query `?token=<raw_key>` (for browser media tags that can't set headers)

Fallback chain in `get_api_key` (deps.py): `Authorization` bearer → `X-Api-Key` → `?token=`.

Also accepted on API-key endpoints: a panel JWT is treated as an implicit full-scope key.

**`GET /api/v1/keys`** — admin. Lists keys (masked). Returns `items[]` with `id,name,scopes,rpm,daily_quota_bytes,backend,storage_chat,expires_at,revoked,prefix`. Adds `used_bytes_today` per key.

**`GET /api/v1/keys/{key_id}`** — admin. Key detail (masked secret).

**`DELETE /api/v1/keys/{key_id}`** — admin. Revoke.

**`PATCH /api/v1/keys/{key_id}`** — admin. Body JSON can include `rpm`, `daily_quota_gb`, `scopes`, `backend`, `storage_chat`.

**`POST /api/v1/keys/{key_id}/rotate`** — admin (if present). New secret, old invalidated.

### 1.3 HMAC preview token (`?ptk=`)

Issued per-file via **`POST /api/v1/files/{file_id}/preview-token`** (requires `read` scope). Query param `?ttl=` (clamped to 10..600s; default 600). Returns:

```json
{"token": "<ptk>", "expires_at": <unix>, "url": "/api/v1/files/<file_id>/preview?ptk=<ptk>"}
```

Accepted **only** on the single file's `/preview` path (`GET /api/v1/files/{file_id}/preview?ptk=...`). It grants `read` preview only — never listing, download, or write. Expires within 10 minutes.

---

## 2. Accounts (Telegram user accounts)

Base `/api/v1/accounts`. Admin JWT or API key with `write`.

**`GET /api/v1/accounts`** — list. Each item includes `pool` (health from TGManager) and `handled_24h`.

**`POST /api/v1/accounts`** — create. (Phone/identity body; triggers session flow.)

**`GET /api/v1/accounts/{id}`** — single account. id can be the account id or phone.

**`PATCH /api/v1/accounts/{account_id}`** — update label + `storage_chat_id`. Body `{"label":"...","storage_chat_id":"..."}`.

**`POST /api/v1/accounts/{account_id}/toggle`** — admin. Enable/disable.

**`DELETE /api/v1/accounts/{account_id}`** — admin. Soft-delete (trash) by default.

**`POST /api/v1/accounts/{account_id}/test`** — admin. Test this specific account's backend. Returns `{"ok":true,"me":"..."}` or `{"ok":false,"error":"..."}`.

**`POST /api/v1/accounts/{account_id}/reset`** — admin. Clear flood/circuit state and reconnect.

### 2.1 Phone login flow (state machine)

`_logins` dict keyed by `login_id`.

**`POST /api/v1/accounts/login/start`** — admin. Body `{"phone":"+989120000000","label":""}`.

- Fake TG mode (`TGDRIVE_FAKE_TG=1` or runtime override): auto-completes, returns `{"login_id":"","status":"ready","account_id":...}`.
- Real mode: requires `tg_api_id`/`tg_api_hash` set (env or runtime DB override). Honors active proxy from `proxy_service.get_active_proxy`. Returns `{"login_id":"<id>","status":"code_sent"}`.

**`POST /api/v1/accounts/login/resend`** — admin. Body `{"login_id":"..."}`. Max 3 resends.

**`POST /api/v1/accounts/login/complete`** — admin. Body `{"login_id":"...","code":"...","password":"..."}`.

- `code` → sign_in. If `SessionPasswordNeededError` → `{"status":"password_needed"}`.
- `password` (2FA) → sign_in(password=...). 3 attempts max → 429.
- On success: persist encrypted StringSession, set status `ready`, refresh into pool. Returns `{"status":"ready","account_id":...}`.

Pending login TTL: 600s. `gc_logins()` drops stale ones.

Runtime note: `_effective_tg_settings()` overlays env settings with the panel's runtime DB overrides (`fake_tg`, `tg_api_id`, `tg_api_hash`) so switching modes from the panel is honored.

---

## 3. Bots

Base `/api/v1/bots`. Admin or key with `write`.

- **`GET /api/v1/bots`** — list.
- **`POST /api/v1/bots`** — add. Body `{"token":"..."}`.
- **`DELETE /api/v1/bots/{id}`** — remove.

Bot tokens feed `BotService`.

---

## 4. Eitaa (eitaa.ir)

Base `/api/v1/eitaa`. Admin or key with `write`.

- **`GET /api/v1/eitaa/accounts`** — list.
- **`POST /api/v1/eitaa/accounts`** — add (username/password, encrypted).
- **`DELETE /api/v1/eitaa/accounts/{id}`** — remove.
- **`POST /api/v1/eitaa/sendFile`** — **API-only send**. Multipart → `{"status":"success","message_id":...}`.

Eitaa constraints (from AGENTS.md):

- Destination must be a public channel and `@sender` becomes a manager there.
- Download is only via the scraper of a public channel page — no API download/delete path.
- **Regex gotcha:** in `_DOWNLOAD` (eitaa_backend.py) the URL literal `?` must be `\?`. The pattern `...{8,64}?token=` otherwise compiles but treats `?` as a lazy quantifier and never matches a real URL `hex?token=`. If scraping returns None, verify the regex against a sample URL with `re.search(pattern, sample_url)` before blaming markup changes.

---

## 5. Channels

Base `/api/v1/channels`. Admin or key with `write`.

- **`GET /api/v1/channels`** — list.
- **`POST /api/v1/channels`** — add/refresh.

Channel scraping (public page) feeds the eitaa backend's file listing for download/preview.

---

## 6. Files

Base `/api/v1/files`. Admin JWT or API key.

### 6.1 Upload

**`POST /api/v1/files/upload`** — direct multipart.

- Header `X-Folder` or query `?folder=` for target path (created on demand). Header `X-Storage-Chat` overrides key's storage_chat (must be `@...` or numeric).
- Body: `file` (UploadFile).
- Runs `require_scope(key, "write")`.
- Respects `max_upload_size` runtime setting (413 if exceeded) and `blocked_extensions` (415).
- Mime from `content_type` else `mimetypes.guess_type`.
- Writes to `final_tmp_dir()/<tmp_id>.bin`, creates file record via `FileRepo.create(...)`, enqueues `"upload"` job at priority 40 with payload `{file_id, tmp_path, size, backend, storage_chat, folder_path, webhook?}`.
- Returns `{"file_id","name","size","status":"queued"}`.
- Quota bytes added via `quota.add(...)`.

**`POST /api/v1/files/upload/session`** — create resumable session.

Body `{"name","size","mime":""}`. Returns `{"session_id","chunk_size":8589934592,"offset":0}`. Creates `upload_sessions` row + empty `.part` file.

**`PATCH /api/v1/files/upload/session/{session_id}`** — push a chunk.

Header `X-Offset` must equal current `offset` (else 409). Body = chunk bytes. On `offset+len >= declared size` → completed: creates file record, enqueues upload job, deletes session, returns `{"offset","completed":true,"file_id"}`. Otherwise `{"offset","completed":false}`.

**`DELETE /api/v1/files/upload/session/{session_id}`** — cancel.

Tombstones session (so racing PATCH gets 410) + deletes `.part` file + unenqueues the upload job. Returns `{"status":"canceled","session_id","bytes_freed","part_bytes"}`. Only the owning key or admin.

### 6.2 List / info

**`GET /api/v1/files`** — list files.

Params: `limit` (max 500, default 50), `offset`, `storage_chat`, `default_channel` (1 = system default channel), `q` (name search), `mime` (prefix), `order` (date|size|downloads|name), `blocked` (1), `trashed` (1).

Returns `{"items":..., "total":..., "limit":..., "offset":...}`.

**`GET /api/v1/files/{file_id}`** — single file record. Includes `parts` (file parts list). Strips `message_ids`.

### 6.3 Delete / restore

**`DELETE /api/v1/files/{file_id}`** — soft-delete (trash) by default. `?purge=true` → enqueues `"delete"` job to remove from Telegram. Returns `{"ok":true,"trashed":...,"restore":"/api/v1/files/{file_id}/restore"}` or `{"ok":true,"purged":...}`.

**`GET /api/v1/files/{file_id}/restore`** — restore (idempotent, GET for panel simplicity).

### 6.4 Streams / preview

**`GET /api/v1/files/{file_id}/download`** — **(the canonical download path)** auth required (`read` scope). Streams file with Range support (resumable). Content-Type from `mime`. Quota bytes added on completion.

**`GET /api/v1/files/{file_id}/content`** — same stream, API-key auth. (Kept for compat; `/download` is primary.)

**`GET /api/v1/files/{file_id}/preview`** — inline preview stream (Range-capable) for browser-viewable media (image/video/audio/pdf/text). Blocked → 403. Not previewable type → 415. Accepts `?ptk=` for share-safe access. Public variant also mounted.

**`GET /api/v1/files/{file_id}/content-type`** — returns the file's content-type/mime (clients use this to decide how to render).

**`GET /api/v1/files/{file_id}/thumb`** — thumbnail image (png/jpeg) if one exists. Generated via `thumbs.py` (Pillow + imageio-ffmpeg for video), constrained by thumbnail settings.

### 6.5 Thumbnails

- Stored per-file; file record carries `thumb_mime` and `thumb_message_id`.
- Generation is async via workers.
- Settings (runtime DB overrides, default from env): `thumb_width`, `thumb_height`, `thumb_max_px`, `thumb_upscale`, `thumb_quality`, `thumb_format`. Geometry reads happen at request time from `runtime_settings()`.
- Video thumbs use imageio-ffmpeg; images use Pillow.

### 6.6 SHA256

- Computed during upload: `sha256_file(path)` streams in `SHA256_CHUNK`-sized chunks and returns hex digest.
- Stored on the file record via `FileRepo.set_sha256(file_id, hex)`.
- Returned in file record as `sha256`. Used for integrity/dedup.

### 6.7 Blocking

**`PATCH /api/v1/files/{file_id}/block`** — body `{"blocked": true/false}`. Blocked files refuse every serving path (panel, presigned, share links) with 403; Telegram copy untouched. Audit logged `file.block` / `file.unblock`.

### 6.8 Presigned / share links

**`POST /api/v1/files/{file_id}/link`** — body `{"ttl":..., "one_time":false}`. Returns presigned download link (HMAC-signed, short TTL). TTL default from `presigned_ttl`.

**`POST /api/v1/files/{file_id}/share`** — body `{"slug":"","password":"","max_downloads":0}`. Creates persistent public link: page `/{slug}` + download `/d/{slug}/dl`. Slug 3-64 chars `[a-zA-Z0-9_-]`. Returns `{"slug","url","download_url","protected","max_downloads"}`.

**`GET /api/v1/files/{file_id}/links`** — list share links for file.

**`PATCH /api/v1/files/{file_id}/links/{link_id}`** — `{"disabled":true/false}`. Quick kill-switch.

**`DELETE /api/v1/files/{file_id}/links/{link_id}`** — delete link.

### 6.9 Public download paths (no admin JWT)

- **`GET /d/{file_id}?exp=&ot=&sig=&jti=`** — presigned download (HMAC-signed). Range-capable. Blocked → 403. Head supported.
- **`GET /{slug}`** — public file page (HTML, RTL Persian, Pixeldrain-style). Enforces password (`?pw=` or `X-Link-Password`), max downloads, disabled. Shows player + QR + download button.
- **`GET /d/{slug}/dl`** — public download through slug link. Increments hits.
- **`GET /{slug}/thumb`** — public thumbnail stream (Cache-Control public, max-age 86400).
- **`GET /{slug}/qr`** — QR code SVG pointing at the public page.

---

## 7. Folders

Base `/api/v1/folders`. Admin or key with `write`.

- **`GET /api/v1/folders`** — list. `?parent=`.
- **`POST /api/v1/folders`** — create. Body `{"name","parent"}`.
- **`GET /api/v1/folders/{id}`** — detail incl. children.
- **`DELETE /api/v1/folders/{id}`** — soft-delete.

Folders are a logical layer over files. Nested upload paths (`projects/2026/reports`) are resolved + created on demand via `FolderRepo.resolve_path(raw, create=True)`.

---

## 8. API keys (see §1.2)

Repeated here for quick scan:

- create/list/revoke/update/rotate under `/api/v1/keys`. Admin only.
- scopes `read,write,admin`; `rpm`; `daily_quota_gb`; `backend`; `storage_chat`.
- Auth: `Authorization: Bearer <key>` / `X-Api-Key` / `?token=`.

---

## 9. Queue / transfers

Base `/api/v1/queue`. Admin JWT.

- **`GET /api/v1/queue/stats`** — queue stats.
- **`GET /api/v1/queue/pressure`** — upload-pressure snapshot (stall + flooded backends + plain summary). Polled by panel banner.
- **`GET /api/v1/queue/jobs`** — list jobs (includes payload + last resumed offset). `?limit=`. Returns `paused`.
- **`GET /api/v1/queue/transfer-progress`** — live in-flight transfer snapshot (files-tab badge). Includes recently-finished (within 3s) so tiny transfers stay visible.
- **`POST /api/v1/queue/pause`** — body `{"kind":"download|upload|delete"}`.
- **`POST /api/v1/queue/resume`** — body `{"kind":"..."}`.
- **`POST /api/v1/queue/resume/{job_id}`** — resume a durable upload job from stored offset (pending/running/retry; not paused). Returns `resumed_offset`.
- **`POST /api/v1/queue/retry/{job_id}`** — re-enqueue a failed job.
- **`POST /api/v1/queue/purge`** — purge finished jobs.

### 9.1 Transfer model

- All transfers go through `TGManager` (shared account pool: `acc:`/`bot:`/`eit:`).
- Enqueue: `POST /api/v1/queue` (or `state.queue.enqueue(kind, payload, priority)`).
- Backend for a transfer is resolved from the file record's backend at enqueue time (snapshot), fallback `TGDRIVE_DEFAULT_BACKEND`.
- Download/delete/stream all go through the manager — never directly to Telethon/API.
- Jobs retry on failure with backoff (default max_retries=5). A test asserting failure must wait until `status=failed`, not a single short poll.
- Delete is soft (trash) by default; `?purge=true` removes from Telegram.

---

## 10. Admin

Base `/api/v1/admin`. Admin JWT only.

- **`GET /api/v1/admin/healthz`** — DB ping. `200 {"ok":true}`.
- **`GET /api/v1/admin/readyz`** — DB + queue + account pool started. `200 {"ok":true}` when ready; else `503 {"ok":false}`. **Container healthchecks should hit this, not `/healthz` — a half-booted container is restarted.**
- **`GET /api/v1/admin/overview`** — aggregate stats (files, traffic, accounts, bots, keys, nodes, queue, uptime, proxies).
- **`GET /api/v1/admin/audit`** — audit log (limit 200).
- **`GET /api/v1/admin/blocked-files-report`** — blocked/unblock actions from audit + file info.
- **`GET /api/v1/admin/metrics`** — Prometheus text format (gauge refresh from live state).
- **`GET /api/v1/admin/health-report`** — deep health: server uptime/mode/queue + session decrypt status for every account/bot + DB counts/integrity. Same builder as `scripts/healthcheck.py`. Rendered by the dashboard's سلامت سیستم card.
- **`GET /api/v1/admin/storage-channels`** — per storage channel: file count, total size, per-type breakdown. Keys pin a dedicated channel (`api_keys.storage_chat`); files fall back to system default.
- **`GET /api/v1/admin/nodes`** — node registry.
- **`GET /api/v1/admin/proxies`** — proxy list.
- **`POST /api/v1/admin/proxies`** — add proxy (share link or host/port/kind).
- **`DELETE /api/v1/admin/proxies/{proxy_id}`** — remove.
- **`PATCH /api/v1/admin/proxies/{proxy_id}`** — `{"enabled":true/false}`.
- **`POST /api/v1/admin/proxies/test`** — speed-test all.
- **`POST /api/v1/admin/proxies/{proxy_id}/test`** — speed-test one.
- **`POST /api/v1/admin/proxies/apply`** — reconnect backends with current proxy decision.
- **`POST /api/v1/admin/janitor/run`** — manual janitor sweep: purge expired upload sessions + tombstoned `.part` tmp files (>1h), old finished jobs (keep 24h), trashed files purge (>7d), revoked keys (>30d), audit/revoked_tokens (>90d). Returns counts.
- **`GET /api/v1/admin/settings`** — runtime settings (editable metadata).
- **`PUT /api/v1/admin/settings`** — save runtime settings (body dict). Returns `applied`.
- **`POST /api/v1/admin/settings/reset`** — reset runtime settings to env defaults.
- **`GET /api/v1/admin/setup/env`** — bootstrap-key status (secrets masked).
- **`PUT /api/v1/admin/setup/env`** — write bootstrap values into `.env` (creates file when missing). `generate_secret` auto-fills `TGDRIVE_SECRET`. Returns `restart_keys` (keys shadowed by process env until restart).
- **`GET /api/v1/admin/setup/status`** — first-run flag + DB engine info.
- **`POST /api/v1/admin/setup/complete`** — finish setup: optional new password, `db_engine` (sqlite/postgres), `default_backend` (telegram/eitaa), `storage_chat`. Marks initialized.
- **`GET /api/v1/admin/backup`** — table-rows JSON backup (base64+gzip; encrypted columns `session_enc/token_enc/pwd_hash` excluded).
- **`POST /api/v1/admin/restore`** — body `{"data":"<base64>"}`. Restores allowed tables (`files,file_parts,tg_accounts,bot_tokens,eitaa_accounts,api_keys,links`).

---

## 11. Configuration (env + runtime DB overrides)

Environment variables (env wins until restart; some are overridable in the DB via settings API):

- `TGDRIVE_SECRET` — signing / presign secret. **Required in production.** The current images ship **no baked default** — set it, or the app starts with a code-level dev default / fake backend.
- `TGDRIVE_FAKE_TG` — `1` = FakeBackend (dev/test). Otherwise real Telegram.
- `TGDRIVE_ADMIN_USERNAME` / `TGDRIVE_ADMIN_PASSWORD` — bootstrap admin. If password empty and `data/initial_admin_password.txt` exists, read from it; else generate + persist (mode 0600).
- `TGDRIVE_PORT` — listen port (default 8000).
- `TGDRIVE_DOWNLOAD_WORKERS`, `TGDRIVE_UPLOAD_WORKERS` — worker counts.
- `TGDRIVE_DEFAULT_BACKEND` — default backend for new files / transfer resolution. Values: `telegram` / `eitaa`.
- `cors_origins` — comma list or `*`.
- `trusted_proxies` — comma list of IPs/CIDRs for X-Forwarded-For trust.
- `log_level`.
- `node_id`.
- `database_url` (PostgreSQL) / SQLite when absent.
- `max_upload_size`, `blocked_extensions`, `presigned_ttl`, `default_key_rpm`, `default_key_daily_quota`, `upload_session_ttl_minutes`, `thumb_*` (width/height/max_px/upscale/quality/format), `proxy_enabled`, etc.

Runtime settings (admin): `GET/PUT /api/v1/admin/settings`. Stored in DB, applied at runtime. Some reads require restart; some are live (`runtime_settings().get_all(db, force=True)` at startup).

---

## 12. Backend model

Multiple backends via `TGManager`:

- `acc:<id>` — Telegram user accounts.
- `bot:<id>` — bot tokens.
- `eit:<id>` — eitaa accounts.

Files record their backend. Transfers, downloads, deletes, streams all go through the manager. There is no local blobstore — Telegram/eitaa is the backing store; local sqlite holds metadata + thumbnails + hashes.

Storage channel model: each key can pin a `storage_chat` (dedicated channel); files without one fall back to the system default channel (`tg_storage_chat`). Admin reports per-channel file count/size/type breakdown.

---

## 13. Common client gotchas

- Use `?token=<key>` when you can't set headers (browser `<img>`/`<video>` loading panel-authenticated streams).
- Use `?ptk=` only for a single file's `/preview` — it won't list or download.
- Delete is soft by default. Use `?purge=true` to actually remove from Telegram.
- Rate limits return `Retry-After`; respect it.
- Large binary endpoints skip access logging (hot paths: `/d/...`, `/api/v1/files/...`).
- Files are identified by id in path params. Some endpoints also accept the file's hash or name.
- For resumable uploads: create session → PATCH chunks with `X-Offset` matching current offset → 409 on mismatch → 410 once canceled.
- Bulk transfer: `POST /api/v1/files/bulk-transfer` with `file_ids` (max 200), `storage_chat` (required, `@...` or numeric), optional `folder_id`. Each file becomes a `transfer` job (parts re-sent, pointers rewritten on success).
- Bulk zip: `POST /api/v1/files/bulk-zip` → signed GET url `/api/v1/files/bulk-zip?ids=&exp=&sig=`. Max 100 files, 2.1GB total. HMAC-signed, short TTL.

---

## 14. Where to look in the code

- auth: `app/api/auth.py`
- accounts: `app/api/accounts.py`
- files + public: `app/api/files.py`
- folders: `app/api/folders.py`
- keys: `app/api/keys.py`
- bots: `app/api/bots.py`
- channels: `app/api/channels.py`
- eitaa: `app/api/eitaa.py`
- queue: `app/api/queue.py`
- admin: `app/api/admin.py`
- deps (auth helpers, rate/quota, client IP, scope): `app/api/deps.py`
- app / routing / lifespan: `app/main.py`
- models/repos: `app/core/models.py`, `app/core/db.py`
- settings: `app/core/settings_service.py`, `app/core/config.py`
- TG layer: `app/tg/manager.py`, `app/tg/fake.py` (FakeBackend for tests)
- queue: `app/queue/queue_manager.py`
- thumbs: `app/services/thumbs.py` (and `app/api/files.py` thumb serving)
- sha256: computed at upload via `sha256_file()` + `SHA256_CHUNK`; stored via `FileRepo.set_sha256`; field `sha256` on the file record
- presign/share: `app/services/presign.py`, `app/core/security.py` (preview token, HMAC)
- health script: `scripts/healthcheck.py`
- proxy: `app/services/proxy_service.py`, `app/api/admin.py` proxy endpoints

---

## 15. Test-mode notes (for the AI verifying against this API)

- Tests run with `TGDRIVE_FAKE_TG=1` and a shared `FakeBackend.STORE`. The store is cleared between tests by the `client` fixture teardown.
- `app/core/state.py` holds a global `state` (DB, manager, queue, bots, janitor, proxy_monitor). The `client` fixture isolates + tears it down per test.
- Temp dirs: the suite creates one `tgdrive-test-*` dir per test and purges it in a `finally`. A session-level fixture sweeps stale leftovers and **fails the run** if any survive (silent leak previously filled the disk). Don't rely on leftover dirs from prior runs.
- Default test admin: username `admin`, password `admin123`.
- Fake backend auto-completes phone login; real mode requires `tg_api_id`/`tg_api_hash`.

---

## 16. Exact endpoints summary (auto-generated style)

Panel JWT auth: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`.

API keys: `GET /api/v1/keys`, `POST /api/v1/keys`, `GET /api/v1/keys/{key_id}`, `DELETE /api/v1/keys/{key_id}`, `PATCH /api/v1/keys/{key_id}`, `POST /api/v1/keys/{key_id}/rotate`.

Accounts: `GET /api/v1/accounts`, `POST /api/v1/accounts`, `GET /api/v1/accounts/{id}`, `PATCH /api/v1/accounts/{account_id}`, `POST /api/v1/accounts/{account_id}/toggle`, `DELETE /api/v1/accounts/{account_id}`, `POST /api/v1/accounts/{account_id}/test`, `POST /api/v1/accounts/{account_id}/reset`, `POST /api/v1/accounts/login/start`, `POST /api/v1/accounts/login/resend`, `POST /api/v1/accounts/login/complete`.

Bots: `GET /api/v1/bots`, `POST /api/v1/bots`, `DELETE /api/v1/bots/{id}`.

Eitaa: `GET /api/v1/eitaa/accounts`, `POST /api/v1/eitaa/accounts`, `DELETE /api/v1/eitaa/accounts/{id}`, `POST /api/v1/eitaa/sendFile`.

Channels: `GET /api/v1/channels`, `POST /api/v1/channels`.

Files: `POST /api/v1/files/upload`, `POST /api/v1/files/upload/session`, `PATCH /api/v1/files/upload/session/{session_id}`, `DELETE /api/v1/files/upload/session/{session_id}`, `GET /api/v1/files`, `GET /api/v1/files/{file_id}`, `DELETE /api/v1/files/{file_id}`, `GET /api/v1/files/{file_id}/restore`, `PATCH /api/v1/files/{file_id}/block`, `POST /api/v1/files/bulk-transfer`, `POST /api/v1/files/{file_id}/preview-token`, `GET /api/v1/files/{file_id}/preview`, `POST /api/v1/files/bulk-zip`, `GET /api/v1/files/bulk-zip`, `POST /api/v1/files/{file_id}/link`, `POST /api/v1/files/{file_id}/share`, `GET /api/v1/files/{file_id}/links`, `PATCH /api/v1/files/{file_id}/links/{link_id}`, `DELETE /api/v1/files/{file_id}/links/{link_id}`, `GET /api/v1/files/{file_id}/download`, `GET /api/v1/files/{file_id}/content`, `GET /api/v1/files/{file_id}/content-type`, `GET /api/v1/files/{file_id}/thumb`.

Public file: `GET /d/{file_id}`, `HEAD /d/{file_id}`, `GET /{slug}`, `GET /d/{slug}/dl`, `GET /{slug}/thumb`, `GET /{slug}/qr`.

Folders: `GET /api/v1/folders`, `POST /api/v1/folders`, `GET /api/v1/folders/{id}`, `DELETE /api/v1/folders/{id}`.

Queue: `GET /api/v1/queue/stats`, `GET /api/v1/queue/pressure`, `GET /api/v1/queue/jobs`, `POST /api/v1/queue/pause`, `POST /api/v1/queue/resume`, `POST /api/v1/queue/resume/{job_id}`, `POST /api/v1/queue/retry/{job_id}`, `POST /api/v1/queue/purge`, `GET /api/v1/queue/transfer-progress`.

Admin: `GET /api/v1/admin/healthz`, `GET /api/v1/admin/readyz`, `GET /api/v1/admin/overview`, `GET /api/v1/admin/audit`, `GET /api/v1/admin/blocked-files-report`, `GET /api/v1/admin/metrics`, `GET /api/v1/admin/health-report`, `GET /api/v1/admin/storage-channels`, `GET /api/v1/admin/nodes`, `GET /api/v1/admin/proxies`, `POST /api/v1/admin/proxies`, `DELETE /api/v1/admin/proxies/{proxy_id}`, `PATCH /api/v1/admin/proxies/{proxy_id}`, `POST /api/v1/admin/proxies/test`, `POST /api/v1/admin/proxies/{proxy_id}/test`, `POST /api/v1/admin/proxies/apply`, `POST /api/v1/admin/janitor/run`, `GET /api/v1/admin/settings`, `PUT /api/v1/admin/settings`, `POST /api/v1/admin/settings/reset`, `GET /api/v1/admin/setup/env`, `PUT /api/v1/admin/setup/env`, `GET /api/v1/admin/setup/status`, `POST /api/v1/admin/setup/complete`, `GET /api/v1/admin/backup`, `POST /api/v1/admin/restore`.
