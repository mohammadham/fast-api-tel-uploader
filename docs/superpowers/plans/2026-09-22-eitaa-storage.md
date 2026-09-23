# Eitaa Storage Backend Implementation Plan

> **For agentic workers:** Steps use checkbox syntax. Native execution (single session).

**Goal:** User chooses per-API-key storage destination (Telegram or Eitaa); admin manages eitaayar tokens; system default configurable via env.

**Architecture:** Second `BackendClient` implementation (`EitaaBackend`, HTTP multipart to eitaayar.ir). Pool manager gains an `eit:{id}` pool selected by `backend` on upload (from key) and download/stream/delete (from file row). Fake backend tags itself by key prefix so tests exercise routing without network.

**Tech Stack:** httpx (already dep), SQLite migration via ALTER, vanilla JS panel.

**Spec:** Eitaayar API research: only `POST /api/{token}/sendFile` (multipart, returns `ok` + `message_id`) and `sendMessage`. No download/delete/validate API → download is best-effort scrape of `eitaa.com/{chat}/{id}` (public storage channel required); delete is a logged no-op.

## Global Constraints
- Default backend setting: `TGDRIVE_DEFAULT_BACKEND` (telegram|eitaa), default telegram.
- Key column `backend` ('' = follow default). File column `backend` ('' = telegram).
- Empty `backend` values must behave exactly as before (zero behavior change for existing files/keys).
- Eitaa errors surface as `SendFailure` → existing retry/circuit-breaker logic handles them.

## Tasks

### Task 1: Schema + repos
- [ ] `db.py`: `eitaa_accounts` table + ALTER `files.backend`, `api_keys.backend` in migration list.
- [ ] `models.py`: `EitaaAccountRepo` (create/list/get/set_enabled/set_status/delete), `ApiKeyRepo.create`+`update` accept backend, `FileRepo.create` accepts backend.

### Task 2: Backends
- [ ] `app/tg/eitaa_backend.py`: send_document (multipart POST), iter_file (scrape public page for file URL, stream with Range passthrough), delete_message no-op warn, close no-op. `storage_chat` attr = configured chat.
- [ ] `fake.py`: instance `kind` from cid prefix (`eit:` → "eitaa").

### Task 3: Manager routing
- [ ] `manager.py`: `_ensure_eitaa`, `acquire(..., backend="")` selects `eit:` pool when backend=="eitaa" (NoBackendAvailable if pool empty), `drop` prefix already generic, `health` unchanged.

### Task 4: Queue + streaming wiring
- [ ] `_handle_upload`: backend from `payload["backend"]`; `_maybe_store_thumb` only for telegram.
- [ ] `_handle_download`/`_handle_delete`: backend from `rec["backend"]`.
- [ ] `streaming.py`: `manager.acquire("acc", backend=rec.get("backend") or "telegram")`.

### Task 5: API
- [ ] `app/api/eitaa.py`: list/add/toggle/delete/test (test = real 1-byte sendFile).
- [ ] `keys.py`: `backend` in create + PATCH.
- [ ] `files.py`: upload payload backend = key override else settings default.

### Task 6: Panel (ui-ux-pro-max tokens, RTL, 44px targets)
- [ ] `index.html`: eitaa tab + add modal; storage `<select>` in key modal; backend badge column in files.
- [ ] `app.js`: loadEitaa/add/toggle/delete/test; key backend field; files badge.

### Task 7: Tests + docs
- [ ] `tests/test_eitaa.py` (~8 tests: routing default/override, eitaa upload→download roundtrip, no-backend failure, toggle/delete, key patch).
- [ ] README, DECISIONS D20, TODO, `.env.example`, AGENTS.md learnings, graphify --update.

## Review Focus
- Existing telegram-only setups: '' backend everywhere → identical behavior (test 0 asserts this).
- Eitaa-stored file public download: scrape failure → clear 503/TransferError, not silent hang.
- Key deleted/repurposed after enqueue: payload backend is snapshotted at enqueue (not re-read at worker time).
