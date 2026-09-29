# TelegramDrive — run doc

## Reproduce artifacts
- venv: `.venv/Scripts/python.exe -m venv .venv` then `.venv/Scripts/python.exe -m pip install -r requirements.txt pytest pytest-asyncio asgi-lifespan` (or `uv pip install -p .venv -r requirements.txt`)
- No `.env` needed for dev preview: run with `TGDRIVE_FAKE_TG=1` (in-memory FakeTelegram) and `TGDRIVE_DATA_DIR=data-livepanel` (auto-created). Admin password auto-generated at `data-livepanel/initial_admin_password.txt` on first start.

## Run server (port 8765, uvicorn from venv, detached, fake-TG)
```powershell
powershell -NoProfile -Command '$env:TGDRIVE_FAKE_TG="1"; $env:TGDRIVE_DATA_DIR="data-livepanel"; $p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8765" -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru; Write-Output ("PID=" + $p.Id)'
```
- Health: `curl http://127.0.0.1:8765/api/v1/admin/healthz` → `{"ok":true}`
- Panel: `http://127.0.0.1:8765/` (login admin / password from `data-livepanel/initial_admin_password.txt`)
- AGENTS.md rule: open `http://127.0.0.1:8765/` in the preview browser first, then run `preview_evaluate` with that tabId — `preview_*` without tabId errors "No browser tab in this task".

## UI session (persisted review login)
- `fetch('/api/v1/auth/login',{...})` → localStorage `td_token`/`td_refresh`
- Channels smoke: `GET /api/v1/channels` → `{"items":[],"default_chat":""}`

## Channels API (verified live)
- Add: `POST /api/v1/channels {chat, label, kind}` → `{id, chat}` (dup 409, bad chat 400)
- Test: `POST /api/v1/channels/{id}/test` → `{ok:true, backend:'acc:1', message_id, chat}`
- Backup: `GET /api/v1/channels/{id}/backup` → `{ok, message_id, backend, name, size, files, bytes}` (manifest uploaded INTO the channel, `#tgdrive-backup` caption; pointer persisted on the row)
- Local copy: `GET /api/v1/channels/{id}/content` → manifest JSON
- Restore: `POST /api/v1/channels/{id}/restore` (merge; existing rows win) → `{ok, message_id, inserted, skipped, file_ids}`

## UI actions — panel functions (panel/src/App.vue)
- Tab «کانال‌ها»: chanSave/chanRename/chanToggle/chanDelete/chanTest/chanBackup/chanRestore/chanFmtBackup
- `backupDB` downloads the gzip backup; `restoreDB(ev)` gunzips via DecompressionStream and POSTs `{"data": <base64gzip>}` matching `RestoreIn{data}` — closes the old FormData/JSON mismatch
- `readLastFile` reads the newest file (`files[0]`, list is newest-first) and opens the preview dialog instead of the invalid keys-POST path

## Real-mode login fix (2026-09-28) — app/api/accounts.py
- Root cause: Telethon's async methods (connect/send_code_request/sign_in/disconnect) were wrapped in asyncio.to_thread(...) → coroutine created but NEVER awaited → instant fake "code_sent"/"ready", no SMS ever sent. Fixed: direct `await client.connect()` / `await client.send_code_request(...)` / `await client.sign_in(...)` / `await client.disconnect()` in login_start + login_complete; `_safe_disconnect` schedules `client.disconnect()` directly.
- login_start/login_complete now honor the panel's runtime settings overlay (fake_tg/tg_api_id/tg_api_hash from runtime_settings()._cache, mirroring TGManager._settings) via `_effective_tg_settings()`.
- login_start uses the active proxy pool (get_active_proxy + build_telethon_proxy + decrypt) — direct fallback on error.
- Empty-creds → clear Persian 400: "API ID / API Hash تلگرام تنظیم نشده …".
- Regression tests: tests/test_login_runtime_settings.py (6 tests, TelegramClient monkeypatched via `telethon.TelegramClient` — no network); manager.refresh_one_account stubbed in complete-tests to avoid real backend spawn. NOTE: project `.env` has dummy TGDRIVE_TG_API_ID=12345678 → to test the no-creds path, zero s.tg_api_id/tg_api_hash on the cached get_settings() object.
- Live verification: POST /login/start {"phone":"+0"} → 3.3s → "invalid phone number format" (real DC round-trip); previously ~500ms fake 200.

## Login UX hardening (2026-09-28, round 2)
- Panel (App.vue): send-code button gets busy+spinner+disabled until response; code step has 2-min resend countdown → `POST /api/v1/accounts/login/resend {login_id}` (max 3, server-side cap via entry.code_resends, resends_left returned); 2FA is its own dialog step 3 (code field hidden/locked, pw attempts capped server-side at MAX_PASSWORD_ATTEMPTS=3 → 429 + login dropped).
- Backend accounts.py: `_drop_login` helper (disconnect+forget); gc_logins (async, TTL 600s) invoked from login_start; login/resend reuses the SAME client/session (send_code_request on existing auth) so no orphan sessions; login_complete now checks `is_user_authorized()` before persisting (dead sessions rejected loudly), 400 details are Persian, refresh_one_account after login is best-effort (log warning) so a proxy hiccup can't turn a saved account into a 500.
- Tests: +2 (resend cap, 2FA cap) → 145 pytest passed; e2e 12 passed; panel build index-CH06zysq.js.

## Real-mode transfer fixes (2026-09-28, round 3) — live-tested with the user's real account+bot
- **telethon_backend.py**: (a) start()/close() awaited directly (to_thread created-but-never-ran coroutines → "Cannot send requests while disconnected" on every op); (b) fresh StringSession has an empty entity cache → warm it with iter_dialogs(limit=500) once per backend instance (200 was NOT enough live: dialog #~300 was the storage channel); (c) `_chat()` normalizes NUMERIC chat strings to int — Telethon treats a numeric string like "-100…" as a phone number → "Cannot find any entity corresponding to …". THIS was the live upload-blocker; (d) BotBackend.send_document gained the caption kwarg (channel probe/backup pass it; missing kwarg → TypeError fallback noise).
- **files.py**: share-link response `download_url` now `/d/{slug}/dl` (was `/d/{slug}` → "invalid or expired signature"; the panel already used the correct path).
- **Panel**: channels tab shows a virtual row for the system-default channel (settings.tg_storage_chat) when no registry row exists (files/bytes from pool stats, needs accounts loaded → switchTab('channels') also calls loaders.accounts); accounts tab "کانال" button opens a select dialog (system default / registered channels / me) instead of window.prompt.
- Live verification: channel test via acc:3 AND bot:2 (probe messages 9797/9798 in the real channel); upload → ready in default channel; /d/{slug}/dl streamed the real file back; PATCH accounts/3 storage_chat; soft-delete (trash).
- Suite: 145 pytest + 12 e2e; panel build index-D3qneRTy.js.

## Suite status (2026-09-28)
- pytest: 143 passed (incl. 6 new tests/test_login_runtime_settings.py)
- panel build: static/assets rebuilt after backend fixes

## Per-channel scope + upload/keys UX (2026-09-29, round 4) — ۶ مورد UX کاربر
- **Keys**: POST /api/v1/keys با storage_chat (select کانال در keyDlg)؛ دکمه «کانال» کلیدها دیگر prompt نیست — دیالوگ keyChatDlg + PATCH /keys/{id} (PATCH/GET همیشه کار می‌کرد؛ مشکل UI بود). NOTE: پاسخ POST کلید `id` ندارد (repo عمداً) — پنل بعد از ساخت لیست را رفرش می‌کند.
- **Files filter**: select کانال در toolbar (values: '' | '__default__' | chat) → GET /files?storage_chat=… یا ?default_channel=1.
- **Upload dialog** (uploadDlg): فایل + پوشه + کانال مقصد؛ نام فایل verbatim می‌رود (سرور فقط _safe_name)؛ هدرهای X-Folder و X-Storage-Chat؛ progress % + MB/s داخل حلقه‌ی chunk (doUpload).
- **Folders per-channel**: ستون folders.scope (sqlite+pg SCHEMA + ALTER)؛ GET/POST /folders با ?scope=؛ view اسکوپ‌دار فقط پوشه‌های همان scope (in_scope در models.py)؛ upload_chunk حالا resolve_path(folder_path, scope=storage_chat) — آپلود کانال‌دار پوشه‌ی scoped همان کانال را می‌گیرد. upload_sessions.storage_chat هم ستون شد.
- **Tests**: tests/test_folder_scopes.py (۲ تست)؛ e2e upload + account-channel دو تست به جریان دیالوگ آپدیت شدند (prompt دیگر نیست؛ برای دیالوگ select اکانت باید کانال با POST /api/v1/channels seed شود).
- Suite: 147 pytest + 12 e2e؛ پنل باندل index-BsSGw9I7.js (حاشه: vite شناسه‌ها را minify می‌کند — برای verify باندل، متن فارسی جست‌وجو کن نه نام متغیر).
- Live (8765, data-livepanel): آپلود 1KB با X-Storage-Chat=-1002296795477 و X-Folder=live-scope/clip → ready، storage_chat و folder_id درست؛ folders با scope دو کانال جدا ایزوله؛ PATCH کلید → storage_chat عوض شد.

## Bots ops + load-pressure wiring (2026-09-29, round 5)
- **Bot test**: POST /api/v1/bots/{id}/test — probes THIS bot via acquire_key + tiny sendDocument to tg_storage_chat (fallback 'me'); status flips ready/error, audited. add_bot now also refresh_one_bot so the backend exists without restart.
- **handled_24h**: upload/delete/download handlers record borrowed.key into payload.handled_by after job done (_last_backend_by_job map; download/delete get __job_id injected). GET /bots and /accounts join a 24h done-jobs count per backend key (AccountRepo.handled_24h_map). Panel: «هندل ۲۴س» column in bots + accounts tables.
- **Pressure settings audit**: download_workers/upload_workers → live resize (set_worker_counts via apply_runtime) ✓; max_concurrent_downloads → per-acc semaphore in manager._sem ✓; circuit breaker (CIRCUIT_THRESHOLD=5/300s) + FloodWait ✓. GAP FIXED: max_concurrent_uploads was defined in EDITABLE_SETTINGS + panel label but NEVER used → now a global asyncio gate around the whole upload send in _handle_upload (async _upload_semaphore, rebuilt on runtime change; env default max=2).
- Gotcha: FakeBackend/BotBackend have no `.key` — use borrowed.key (caught by test_delete_queues_job).
- Tests: tests/test_bots_ops.py (4 tests) → 151 pytest + 12 e2e; panel bundle index-Ct1ThFHn.js.
- Live (8765): bot:2 test ok (probe 5557); upload → acc handled_24h=1; PUT settings max_concurrent_uploads 3→2 round-trip works.

## Bulk multi-select transfer (2026-09-29, round 6)
- **POST /api/v1/files/bulk-transfer** {file_ids, storage_chat, folder_id?}: validates target (@/numeric), dedupes, skips not-ready/already-there/no-parts (per-file reasons), optional folder re-home, enqueues kind='transfer' jobs (priority 30, max 200/request).
- **Transfer job** (_handle_transfer): downloads each part from src chat → send_document verbatim (name/mime + folder hashtag caption) into target chat → set_parts() rewrites storage_chat/message_ids/parts. Old copies stay in the source chat (safety net); failures retry with backoff leaving the record untouched.
- Queue routing: transfer shares the upload worker pool (PG claim kind IN (upload,transfer); sqlite _next_job defers it on download workers; downloads-first guard does not apply).
- Panel: bulk mode gains «انتقال گروهی به کانال…» + dialog (channel select from registry/default + folder select untouched/root/any folder) → /files/bulk-transfer.
- FileRepo.set_parts new; KIND_TRANSFER constant; job stats by_kind includes transfer.
- Tests: tests/test_bulk_transfer.py (4) → 155 pytest + 12 e2e; bundle index-Cj5OLOCs.js. Live: upload 512B → bulk-transfer → ready in -1002296795477 with new message_id.

## Channel stats in list + filter select (2026-09-29, round 7)
- GET /api/v1/channels already aggregated ready/non-deleted files+bytes per chat (_stats_by_chat, '' bucket = empty storage_chat); now also returns default_stats for the system-default chat (works even when tg_storage_chat='' → the ''/Saved bucket). Gotcha: `stats.get(default_chat or "__none__")` broke the ''-bucket — default_chat '' must still read bucket ''.
- Panel: channels tab virtual default row shows real files/bytes (channelDefaultStats ref); files-tab channel select shows counts in options («کانال پیش‌فرض (chat) — N» / «chat — N» و «همه کانال‌ها (total)»); switchTab('files') now also fires loaders.channels() so the select counts are fresh.
- Tests: tests/test_channel_stats.py (2) → 157 pytest + 12 e2e; bundle index-Ci_l10iZ.js. Live: @-1002296795477 files=2/1536B; default_stats files=2 bytes=9.6MB.

## Drag & drop upload (2026-09-29, round 8)
- Dropping files on the files tab opens the SAME upload dialog (uploadDlg) prefilled: folder = filesFolderDraft (current folder), channel = active channel filter ('' when default-filter so the key/system default wins); multi-file drop → input now multiple, dialog lists count+total size; uploadStart queues first file (existing per-file flow).
- dragenter/leave depth counter guards the overlay flicker; overlay hidden while dialog open; drop while trash view visible switches filesTrashed off (uploads never target trash); section got position:relative so the overlay covers only the section.
- e2e: new test dispatches dragenter+drop with a DataTransfer (playwright dispatchEvent) asserting overlay → dialog → queued file. 13 e2e + 157 pytest; bundle index-BREDMSzH.js.
