# TelegramDrive REST API Documentation

## Base URL
```
http://your-server:8000/api/v1
```

All endpoints require authentication unless otherwise noted. Use the Bearer token obtained from `/api/v1/auth/login` in the `Authorization` header:
```
Authorization: Bearer <access_token>
```

---

## Authentication Endpoints

### POST `/api/v1/auth/login`
Login with admin credentials.

**Request:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1440  // minutes
}
```

**Errors:**
- `401`: Invalid credentials

---

### POST `/api/v1/auth/refresh`
Refresh access token using refresh token.

**Request:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Errors:**
- `401`: Invalid or expired refresh token

---

### POST `/api/v1/auth/logout`
Logout and invalidate tokens.

**Request:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:** `200 OK`

---

### GET `/api/v1/auth/me`
Get current user info.

**Response:**
```json
{
  "username": "admin",
  "is_active": true,
  "is_superuser": false
}
```

---

## Accounts Endpoints

### GET `/api/v1/accounts`
List all Telegram accounts.

**Response:**
```json
{
  "items": [
    {
      "id": "123456789",
      "label": "My Phone",
      "phone": "+989123456789",
      "status": "ready",
      "enabled": true,
      "uploads_done": 15,
      "downloads_done": 42,
      "bytes_up": 20971520
    }
  ],
  "total": 1
}
```

**Errors:** `401` - admin auth required

---

### POST `/api/v1/accounts/login/start`
Start the login process for a new account.

**Request:**
```json
{
  "phone": "+989123456789",
  "label": "My Phone (work)"
}
```

**Response:**
```json
{
  "status": "ready",  // or "code_sent"
  "login_id": "optional_login_id",
  "msg": "Code sent to phone"
}
```

**Errors:** `400` - invalid phone format

---

### POST `/api/v1/accounts/login/complete`
Complete the login process with the verification code.

**Request:**
```json
{
  "login_id": "optional_login_id_from_start",
  "code": "12345",
  "password": "optional_2fa_password"
}
```

**Response:**
```json
{
  "status": "password_needed",  // or "ready"
  "msg": "2FA password required"  // if applicable
}
```

**Errors:** `400` - invalid code or login_id

---

### PATCH `/api/v1/accounts/{account_id}`
Update an account.

**Response:** Updated account object

**Errors:** `401` - admin auth, `404` - account not found

---

### POST `/api/v1/accounts/{account_id}/toggle`
Enable/disable an account.

**Response:**
```json
{
  "id": "123456789",
  "enabled": true
}
```

---

### DELETE `/api/v1/accounts/{account_id}`
Delete an account.

**Response:** `200 OK`

**Errors:** `401` - admin auth

---

### POST `/api/v1/accounts/{account_id}/test`
Test an account connection.

**Response:**
```json
{
  "ok": true,
  "error": null
}
```

**Errors:** `400` - connection failed with error details

---

### POST `/api/v1/accounts/{account_id}/reset`
Reset account state (e.g., for 2FA).

**Response:** `200 OK`

---

## Bots Endpoints

### GET `/api/v1/bots`
List all Telegram bots.

**Response:** Array of bot objects with id, label, status, last_error, etc.

---

### POST `/api/v1/bots`
Add a new bot.

**Request:**
```json
{
  "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123e4A",
  "label": "My Bot"
}
```

**Response:** Created bot object

---

### POST `/api/v1/bots/{bot_id}/toggle`
Enable/disable a bot.

**Response:** Updated bot object with enabled status

---

### DELETE `/api/v1/bots/{bot_id}`
Delete a bot.

**Response:** `200 OK`

---

## Eitaa Endpoints

### GET `/api/v1/eitaa`
List all eitaa accounts.

**Response:** Array of eitaa account objects

---

### POST `/api/v1/eitaa`
Add a new eitaa account.

**Request:**
```json
{
  "token": "eitaayar_token_from_eitaayar.ir",
  "chat_id": "123456789",  // target channel ID
  "label": "My Eitaa Channel"
}
```

**Response:** Created eitaa account object

---

### POST `/api/v1/eitaa/{eitaa_id}/toggle`
Enable/disable eitaa account.

**Response:** Updated eitaa account object

---

### DELETE `/api/v1/eitaa/{eitaa_id}`
Delete eitaa account.

**Response:** `200 OK`

---

### POST `/api/v1/eitaa/{eitaa_id}/test`
Test eitaa account connection.

**Response:**
```json
{
  "ok": true,
  "error": null
}
```

---

## Files Endpoints

### POST `/api/v1/files/upload`
Upload a file (multipart).

**Request:** `multipart/form-data` with file field

**Response:**
```json
{
  "file_id": "unique_file_id",
  "name": "filename.txt",
  "size": 1024,
  "status": "ready"
}
```

**Errors:** `401` - auth required, `400` - invalid file

---

### POST `/api/v1/files/upload/session`
Create a resumable upload session.

**Request:**
```json
{
  "name": "large_file.txt",
  "size": 104857600,  // 100MB
  "mime": "application/octet-stream"
}
```

**Response:**
```json
{
  "session_id": "session_unique_id",
  "chunk_size": 8388608,  // 8MB default
  "offset": 0
}
```

---

### PATCH `/api/v1/files/upload/session/{session_id}`
Upload a chunk in resumable upload.

**Headers:** `X-Offset: <byte_offset>`

**Response:**
```json
{
  "offset": 10485760,  // new offset
  "completed": false  // true when finished
}
```

**Errors:** `400` - invalid offset, `401` - auth required

---

### GET `/api/v1/files`
List all files.

**Response:**
```json
{
  "items": [
    {
      "id": "f_abc123",
      "name": "filename.txt",
      "size": 1024,
      "status": "ready",
      "backend": "telegram",
      "downloads": 5
    }
  ],
  "total": 1
}
```

---

### GET `/api/v1/files/{file_id}`
Get file info.

**Response:**
```json
{
  "id": "f_abc123",
  "name": "filename.txt",
  "size": 1024,
  "status": "ready",
  "backend": "telegram",
  "created_at": "2026-01-15T10:30:00Z",
  "downloads": 5,
  "sha256": "hash_value"
}
```

---

### DELETE `/api/v1/files/{file_id}`
Delete a file.

**Query Parameters:**
- `?purge=true` - Also delete from Telegram (if applicable)

**Response:** `200 OK`

**Errors:** `401` - auth required

---

### POST `/api/v1/files/{file_id}/link`
Create a public share link for a file.

**Request:**
```json
{
  "slug": "optional-custom-slug",  // auto-generated if empty
  "password": "optional_password"  // if set, link is protected
}
```

**Response:**
```json
{
  "url": "http://your-domain.com/s/abc123def456",
  "slug": "abc123def456",
  "password": null,  // if set
  "expires_at": "2026-12-31T23:59:59Z"  // if applicable
}
```

---

### GET `/api/v1/files/{file_id}/content`
Download file content.

**Response:** File bytes (streamed)

**Errors:** `404` - file not found, `403` - access denied

---

### POST `/api/v1/files/{file_id}/share`
Create a share link (alternative to /link endpoint).

**Request:**
```json
{
  "slug": "custom-slug-or-empty",
  "password": "password-or-empty"
}
```

**Response:** Same as `/link` endpoint

---

### GET `/api/v1/files/{file_id}/links`
List all share links for a file.

**Response:**
```json
[
  {
    "id": "link_abc123",
    "slug": "abc123",
    "password_protected": false,
    "created_at": "2026-01-15T10:30:00Z",
    "expires_at": null,
    "download_count": 12
  }
]
```

---

### DELETE `/api/v1/files/{file_id}/links/{link_id}`
Delete a share link.

**Response:** `200 OK`

---

### POST `/api/v1/files/{file_id}/restore`
Restore a deleted file.

**Response:** `200 OK`

**Errors:** `401` - admin auth required

---

### GET `/api/v1/files/{file_id}/download`
Download a file with tracking.

**Response:** File bytes

---

## Queue Endpoints

### GET `/api/v1/queue/stats`
Get queue statistics.

**Response:**
```json
{
  "pending": 3,
  "running": 1,
  "completed": 42,
  "failed": 2,
  "paused": false
}
```

---

### GET `/api/v1/queue/jobs`
List all queue jobs.

**Response:** Array of job objects with id, kind, priority, status, error, attempts

---

### POST `/api/v1/queue/pause`
Pause upload queue.

**Request:**
```json
{
  "kind": "upload"
}
```

**Response:** `200 OK`

---

### POST `/api/v1/queue/resume`
Resume upload queue.

**Response:** `200 OK`

---

### POST `/api/v1/queue/retry/{job_id}`
Retry a failed job.

**Response:** `200 OK`

---

### POST `/api/v1/queue/purge`
Purge all completed/failed jobs.

**Response:** `200 OK`

---

## Admin Endpoints

### GET `/api/v1/admin/overview`
Get dashboard overview statistics.

**Response:**
```json
{
  "files": {
    "total": 150,
    "ready": 120,
    "bytes_stored": 5368709120  // 5GB
  },
  "traffic": {
    "bytes_served": 10737418240,  // 10GB
    "downloads": 245
  },
  "accounts": {
    "total": 5,
    "ready": 5
  },
  "bots": 3,
  "api_keys": 10,
  "queue": {
    "pending": 0,
    "running": 0
  },
  "uptime": 3600  // seconds
}
```

**Auth required:** Yes - admin token

---

### GET `/api/v1/admin/audit`
Get audit log.

**Query Parameters:**
- `limit`: int (default 200, max 1000)

**Response:**
```json
{
  "items": [
    {
      "ts": 1790192731.975,
      "level": "INFO",
      "logger": "tgdrive.access",
      "method": "POST",
      "path": "/api/v1/auth/login",
      "status": 200,
      "duration_ms": 45,
      "ip": "127.0.0.1",
      "request_id": "abc-123",
      "correlation_id": "def-456"
    }
  ]
}
```

---

### GET `/api/v1/admin/metrics`
Get Prometheus metrics.

**Response:** Plain text metrics format

---

### GET `/api/v1/admin/healthz`
Health check endpoint.

**Response:**
```json
{
  "ok": true
}
```

---

### GET `/api/v1/admin/readyz`
Readiness check for load balancers.

**Response:**
```json
{
  "ok": true
}
```

**Errors:** `503` - queue or manager not initialized

---

### GET `/api/v1/admin/backup`
Create a database backup.

**Response:**
```json
{
  "backup": "base64_encoded_gzipped_csv_dump"
}
```

---

### POST `/api/v1/admin/restore`
Restore database from backup.

**Request:** `multipart/form-data` with `backup` file field

**Response:** `200 OK` with toast notification in UI

**Errors:** `400` - invalid backup format

---

## Error Response Format

All error responses follow this format:
```json
{
  "detail": "Error description message"
}
```

**HTTP Status Codes:**
- `400`: Bad request - invalid input
- `401`: Unauthorized - missing or invalid auth token
- `403`: Forbidden - insufficient permissions
- `404`: Not found - route or resource not found
- `409`: Conflict - duplicate entry, etc.
- `422`: Validation error
- `429`: Too many requests - rate limited
- `500`: Internal server error
- `503`: Service unavailable - queue or dependencies not ready

---

## Rate Limiting

The API implements rate limiting per API key:
- **Default:** 120 requests per minute per key
- **Daily quota:** 100GB per key per day
- Headers include: `X-RateLimit-Remaining`, `X-RateLimit-Reset`

---

## Webhooks

Currently, the API does not support webhooks. For real-time notifications, use:
- Polling the queue status endpoint
- WebSocket connections (if enabled in future versions)
- Telegram bot updates (for account events)

---

## Versioning

API version: `v1`

**Backward compatibility:** Major versions will be incremented for breaking changes. Minor versions may add new endpoints without breaking existing ones.

**Deprecation policy:** Endpoints will be deprecated at least 30 days before removal. Deprecated endpoints will return `X-Deprecated: true` header and `deprecation-date` header with removal date.

---

## Examples

### Python Example - Login
```python
import httpx

async def login():
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8000/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"}
        )
        data = resp.json()
        token = data["access_token"]
        return token
```

### Python Example - Upload File
```python
import httpx

async def upload_file(token, file_path):
    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                "http://localhost:8000/api/v1/files/upload",
                files={"file": f},
                headers={"Authorization": f"Bearer {token}"}
            )
        return resp.json()
```

### Python Example - Create Share Link
```python
import httpx

async def create_share(token, file_id, slug="my-file"):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"http://localhost:8000/api/v1/files/{file_id}/link",
            json={"slug": slug},
            headers={"Authorization": f"Bearer {token}"}
        )
        return resp.json()
```
## Admin: Runtime Settings / Setup / Nodes (v2.1)

All endpoints require admin JWT (panel login).

### GET /api/v1/admin/settings
List editable runtime settings with effective value + source.

Response: `{"items": [{"key","group","type","description","current","env_value","db_value","source","read_only"}]}`

Groups: `limits`, `links`, `queue`, `backend`.
Keys: `max_upload_size`, `split_threshold`, `default_key_rpm`, `default_key_daily_quota`,
`blocked_extensions`, `presigned_ttl`, `upload_session_ttl_minutes`, `job_max_retries`,
`download_workers`, `upload_workers`, `max_concurrent_downloads`, `max_concurrent_uploads`,
`default_backend`.

Priority: DB value (saved from panel) > env/config default. Worker-count changes are
applied live (no restart). Sensitive keys (secret, db_path, redis_url, admin creds)
remain env-only.

### PUT /api/v1/admin/settings
Body: `{"<key>": value, ...}` — validates type/range; unknown keys → 400.
Applies live side effects (worker resize). Audited as `settings.update`.

### POST /api/v1/admin/settings/reset
Delete all DB overrides → back to env defaults.

### GET /api/v1/admin/setup/status
`{"initialized": bool, "initialized_at": float}` — starter flag stored in `system_meta`.
Fresh installs show `initialized: false` until the panel wizard completes.
Existing installs are auto-marked initialized on startup (env password or existing data).

### POST /api/v1/admin/setup/complete
Body: `{"new_password": "", "default_backend": "telegram"|"eitaa"}` (both optional).
Sets the admin password (min 6 chars) and/or default backend, then flips the
`initialized` flag. Audited as `setup.complete`.

### GET /api/v1/admin/nodes
Node registry for multi-server mode: `{"items": [{"node_id","hostname","version",
"started_at","last_heartbeat","workers_dl","workers_ul"}], "node_id": <this node>}`.
Nodes heartbeat every 30s. In SQLite single-server mode one node (this host) appears.

### GET /api/v1/admin/backup  |  POST /api/v1/admin/restore
Rewritten v2.1. Backup returns JSON `{ts, tables, data}` where `data` is
base64(gzip(JSON rows)) for files/file_parts/tg_accounts/bot_tokens/eitaa_accounts/
api_keys/links — encrypted secrets (sessions/tokens) are excluded.
Restore body: `{"data": "<same base64 payload>"}`; rows are inserted idempotently
(INSERT OR IGNORE / ON CONFLICT DO NOTHING).

## Multi-server deployment (v2.1)

- Set the **same `TGDRIVE_SECRET` and `TGDRIVE_FERNET_KEY`** on every node (sessions,
  JWTs and presigned links are verified independently per node).
- Point every node at the shared Postgres: `TGDRIVE_DATABASE_URL=postgres://...`
- Give every node a unique `TGDRIVE_NODE_ID` (default: hostname).
- Optional shared Redis: `TGDRIVE_REDIS_ENABLED=true` + `TGDRIVE_REDIS_URL`.
- Job claiming on Postgres is atomic (UPDATE...RETURNING with lease semantics);
  leases renew every 60s and expired leases from dead nodes are stolen automatically.
- **Upload jobs are sticky to their origin node** (the tmp file lives on that node's
  disk). If a node dies mid-upload the job fails after retries — re-upload required.
- Runtime settings are shared via the `settings` table (≤10s convergence) and worker
  resize applies on every node.

## Admin: Telegram Proxy Pool (v2.2)

All endpoints require admin JWT. Global on/off + strategy live in runtime
settings: `proxy_enabled` (0/1) and `proxy_strategy` (`speed`|`rr`) under the
"proxy" group of GET/PUT /api/v1/admin/settings.

### GET /api/v1/admin/proxies
List proxies sorted for selection: tested-by-latency ascending first, then
untested. Row: `{id,label,kind,host,port,username,secret_hex,enabled,status,
latency_ms,last_checked_at,last_error,created_at}`. `status`: ok|degraded|down|unknown.
Passwords are never returned (encrypted at rest).

### POST /api/v1/admin/proxies
Body option A: `{"link": "tg://proxy?server=..&port=..&secret=.."}` (also accepts
`t.me/proxy` links, `socks5://user:pass@host:port`, bare `host:port[:user:pass]`).
Option B (manual): `{"host","port","kind":"mtproto|socks5|http","username","password","secret_hex","label"}`.
Returns `{ok:true,id}`. Invalid link/format → 400.

### PATCH /api/v1/admin/proxies/{id}  Body: {"enabled": bool}
### DELETE /api/v1/admin/proxies/{id}

### POST /api/v1/admin/proxies/test
Concurrent TCP-connect speed test of all proxies; results persisted and rows
returned re-sorted by latency. Per-proxy: POST /api/v1/admin/proxies/{id}/test.

### POST /api/v1/admin/proxies/apply
Drop live telegram connections and reconnect with the current proxy decision
(also happens automatically when proxy settings change or proxies are mutated).

### Selection semantics
When `proxy_enabled=0` (default) all connections are direct. When enabled, new
account connections use the best proxy: fastest tested (speed strategy) or
round-robin across tested proxies (rr). Untested proxies are used only if no
tested one is healthy.

### Periodic health monitor (v2.3)
`ProxyMonitor` (always running) re-tests the whole pool every
`proxy_monitor_interval` minutes — new key in the settings "proxy" group
(`0` = off, default; `2..1440` otherwise, panel-validated). On each pass it
persists status/latency, lifts fallback dead-marks for proxies that test
healthy, and notifies admins via the bot notify path **only when a proxy's
status flips** (ok→down, down→ok, degraded↔ok) — restarts never trigger an
alert storm. Alerts read: "🛰 گزارش سلامت پراکسی‌ها" + one line per flip +
usable-proxy count.

### Fallback (v2.3)
When a transfer through a proxied backend fails at the transport level
(FloodWait excluded), TGManager reports that proxy dead; new connections
automatically use the next-fastest healthy proxy (or direct when none is
usable). Dead-marks expire after 5 minutes or as soon as a monitor pass /
successful transfer proves the proxy healthy.
