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

## Persistent upload tray (2026-09-29, round 9)
- uploadJobs reactive array + runUploadJob: «شروع آپلود» hands files to the tray and CLOSES the dialog immediately — uploads continue in background (one AbortController per job). doUpload(file, folder, chat, job) updates job.pct/job.speed (legacy uploadPct globals kept for the never-shown inline bar; startResumableUpload/cancelXHR legacy untouched).
- #upload-tray fixed bottom-left: active/total counters, per-job name+folder/channel tags+badge (در حال آپلود/صف شد/لغو شد/خطا), per-job progress-track with % and MB/s, per-job cancel (لغو) for uploading, × dismiss for finished, «پاک‌سازی تمام‌شده‌ها» clears non-active.
- e2e: tray test (visible outside dialog → done badge → dismiss → cleanup) + drag&drop now asserts the tray; updated old upload test's "no progress-track" assertion (inline bar replaced by tray). 14 e2e + 157 pytest; bundle index-D4foyWVf.js.

## Global upload cancel (round 10)
- DELETE /api/v1/files/upload/session/{sid}: ownership (uploader column, new migration), tombstone (offset=-1) → racing chunk PATCH = 410 "upload session canceled"; re-cancel = 410; .part tmp unlinked; janitor TTL purge still covers tombstones.
- Panel tray cancel: aborts fetch + fire-and-forget DELETE (also covers cancel-before-create via job.sessionId post-create check); badge shows «در حال لغو…».
- Tests: tests/test_upload_cancel.py (3) → 160 pytest + 16 e2e; bundle index-BjompV5h.js; live cycle on 8765 verified (200/410/410 + part deleted).

## Real sequential batch upload (round 11)
- uploadStart/onTrayDrop now enqueue via enqueueUploadJobs(pairs): jobs get batchTotal/batchIndex; a module-level uploadChain promise tail-queues runUploadJob so only ONE file uploads at a time (still one HTTP upload at a time, no interleave between batches). Each row keeps its own pct/speed.
- Tray row badge: queued job = «در صف», running = «فایل k از n» (single file = «در حال آپلود»); header adds «N در صف» segment; early-cancel of queued jobs skips the network (runUploadJob guard) and started flag set only when the job actually begins.
- Tests: e2e "real batch upload…" (10MB×2 with 350ms route delay: فایل 1 از 2 + در صف → صف شد ×2, both on server) → 17 e2e + 160 pytest; bundle index-B0QyFXz3.js.

## Upload-pressure alerts: stall + flood (round 16)
- **Stall detection (app/queue/queue_manager.py):** the global upload gate (`max_concurrent_uploads`) now tracks jobs blocked inside `_handle_upload` (`_gate_waiters`) plus still-pending runnable jobs while the gate is full or every upload backend is flooded (`_waiting_uploads()` — single source of truth for the alert, `stats().stall` and the endpoint). `check_upload_stall()` fires once `waiting>0 && oldest_waiting_s >= upload_stall_threshold_s` (0=off): metric `queue.stall_alert`, Persian admin notification, and a clear-notice on recovery. It runs on a dedicated **5s `_stall_loop`** — NOT the 30s node heartbeat: a live 20s stall landed between two heartbeat ticks and produced no alert (observed, then fixed).
- **Flood tracking (app/tg/manager.py):** per-backend `_flood_until` + consecutive-FloodWait counts; `flood_alert_threshold` consecutive floods on one backend → cooldown-limited (`flood_alert_cooldown_s`) admin alert via app/services/notify.py.
- **Endpoint** GET `/api/v1/queue/pressure` (admin): `{stall:{stalled,since,waiting,oldest_waiting_s,threshold,gate_full,gate_capacity,upload_workers}, flooded:[...], summary}`; summary = «N جاب آپلود منتظر اسلات آزاد (سقف K)» or, once stalled, «صف آپلود معطل است: N جاب پشت سقف همزمانی (X ثانیه؛ سقف K)».
- **Panel:** global `.pressure-banner` between header and main (visible on any tab) + `.pressure-card` in the queue tab, both fed by a 5s poll; new settings labels in App.vue. New runtime settings: `upload_stall_threshold_s` (default 120), `fake_send_delay_s` (fake-TG slow-send lever, 0=off), `flood_alert_threshold` (3), `flood_alert_cooldown_s` (600).
- **Gotchas (both cost live-debugging hours):** (1) with `upload_workers == max_concurrent_uploads` (default 2/2) the gate can never block a worker → `waiting` stays 0 and a stall is *impossible*; repro needs `upload_workers > max_concurrent_uploads`. (2) `fake_send_delay_s` was only applied in `_ensure_account`, so in a pool containing a bot (`bot:2`) 3 of 4 uploads went through bots instantly — the delay now applies to acc/bot/eit fake backends alike (`_fake_send_delay()`); the old «۲ فایل +۰s و یکی +۵s» pattern was this, not a queue bug. Also: new backends read the delay at spawn, so PUT + `POST /api/v1/admin/proxies/apply` (reload_all) is required for it to take effect.
- **Live (8765, settings reset afterwards):** PUT delay=5/threshold=3/workers=4 + proxies/apply → 4×8MB uploads → 16 samples `wait=4→2`, `gate_full=true`, «…منتظر اسلات آزاد (سقف ۲)», drain 12.4s; delay=20 run → `stalled=true` («صف آپلود معطل است…»), metric `queue_stall_alert_total 1`, log «upload queue stalled». Reset to delay 0 / threshold 120 / workers 2.
- **Tests:** tests/test_pressure_alerts.py (5: alert by job age, clear notice, flood threshold+cooldown, endpoint shape); e2e «upload pressure banner…» (PUT delay 6 + proxies/apply, 4×8MB parallel, poll /queue/pressure until `waiting>0 && gate_full`, then assert banner + queue card while still stalled) — note the stall window must stay longer than the panel's 5s poll or the banner check misses it. Full: **170 pytest, 21 e2e (3.0m)**. Bundles `index-jk2t3Sy-.js` / `index-_bEs7tOq.css`.

## Periodic janitor cleanup (round 17)
- **Feature (app/services/janitor.py):** `Janitor` runs a 600s loop; `_sweep()` does:
  1. deletes tmp files in `final_tmp_dir()` (runtime `upload_tmp_dir or data_dir/tmp`) older than **1 hour** (mtime cutoff, with 2s backoff if the dir is momentarily unreadable);
  2. deletes **expired upload sessions** (`created_at < now() - upload_session_ttl_minutes`, runtime TTL wins over env) and their `{session}.part` temp files;
  3. purges **old finished jobs** (`purge_finished` older than 24h);
  4. schedules **trashed files** older than 7 days for telegram deletion (soft-delete → trash, `?purge=true`);
  5. revokes `api_keys` older than 30d, clears `audit_log`/`revoked_tokens` older than 90d.
- **Test (tests/test_janitor_tmp_cleanup.py, 3 tests):** stale tmp file + its `.part` deleted while a recent draft is kept; 2 expired sessions + parts deleted while 1 fresh session is kept; recent files (<1h) kept. Runs on `app/core/db.py` `Database` via `db.execute` (raw SQL, since `UploadSessionRepo` has no list() — created_at must be inserted old, because `create()` always uses wall-clock `now()`).
- **Live:** ran on the 8765 dev server with `fake_tg=1`; log line `janitor: N tmp, 0 sessions, 0 jobs, 0 trash-purged, 0 revoked-keys, 0 audit, 0 tokens` confirmed the sweep executes every 600s. Settings reset to delay 0 / threshold 120 / workers 2 afterwards.

## Bulk zip download (round 15)
- **Endpoint pair (app/api/files.py):** POST `/api/v1/files/bulk-zip` {file_ids} (key auth, scope read) validates ≤100 files, skips not-ready/blocked/trashed (skipped[] in the response), caps the summed size at max_upload_size → returns {url, expires_at, files, bytes, skipped}. GET `/api/v1/files/bulk-zip?ids=<json>&exp&sig` (no auth; sig-only) streams the archive — registered BEFORE `@router.get("/{file_id}")` or the literal route never matches (symptom: 401 «missing API key» on a sig-only GET).
- **Streaming zip writer (app/services/streaming.py: zip_stream_response):** hand-rolled because `zipfile.ZipFile.open(w)` rewrites the local header with seek() — here local headers carry flag bit 3 (data descriptor, sizes/сrc in the trailing PK\x07\x08), zlib raw deflate (wbits=-15), then hand-built central directory + EOCD. Each entry is materialized to `data_dir/tmp/zip_<fid>.bin`, compressed+yielded, unlinked immediately; ONE backend borrow for the whole archive, released by `_ZipBackground` after the response.
- **Sig:** `verify_str` (app/core/security.py) = compare_digest(sign_str) — query = `zip:{ids_json}:{exp}`, TTL 600s.
- **Panel:** «دانلود گروهی (zip)» button in the bulk-select action bar (App.vue bulkZipDownload: mint → toast «آرشیو N فایل آماده شد» → click `<a>`; skipped files are mentioned in the toast).
- **Tests:** tests/test_bulk_zip.py (2: roundtrip+dupe-name via infolist/open, tampered sig 403 + expired 403 + skip-missing + empty selection 400) → 165 pytest; e2e "bulk zip download…" (per-run unique names, waitForEvent("download") + download.path() + fs.readFileSync, size cross-checked) → 20 e2e; bundle **index-I-gIafXb.js**.
- **Gotchas:** (1) POST url contains literal `["f_.."]` — curl treats `[...]` as a glob → percent-encode (`%5B..%5D`) or `curl --globoff`; (2) routes order; (3) Git Bash maps `/tmp` to `C:\Users\<u>\AppData\Local\Temp` for its own tools but NOT for Windows python — pass the Windows path explicitly.
- **Live (8765):** upload 2 files → POST bulk-zip → GET → 200 application/zip, 333B, testzip=None, contents round-trip exact. Old server process needed a restart first (sig-only GET hit `/{file_id}` → 401); fake_tg=1 already in the live DB.

## Auto source-cleanup after transfer (round 14, transfer_delete_source)
- `_handle_transfer` after set_parts(): if `transfer_delete_source` (runtime setting, default **off**) is 1, re-borrows a backend and deletes every source part (delete_message on the OLD chat). Best-effort: try/except at every level — a failed source delete NEVER fails the already-successful transfer (no release(exc) → no circuit-breaker poisoning). Runs AFTER the first borrow is released (no double-borrow); borrow failure only logs a warning.
- Setting: `transfer_delete_source` (int 0/1, _int_flag) in EDITABLE_SETTINGS + group telegram_api + Settings field (bool, default False). Panel: select خاموش/روشن در تب تنظیمات → «تلگرام و ایتا» (optionedSettings + settingLabels).
- Tests: tests/test_transfer_source_cleanup.py (2) — on: FakeBackend.STORE entries GONE after transfer + file ready in target; off: source copies stay. → 163 pytest + 19 e2e; bundle index-bBVh8Yn1.js. e2e transfer-badge test now PUTs the setting on (finally restores the prior value).
- Live gotcha: the settings DB had fake_tg=0 which OVERRIDES the env TGDRIVE_FAKE_TG=1 → backends never load («no telegram backend configured» on every upload). Fix: PUT /admin/settings {"fake_tg": 1} (no restart needed — apply_runtime reloads backends). If live 8765 suddenly can't upload: check GET /admin/settings fake_tg FIRST, not the env.
- Live cycle verified: upload → transfer done → file in -1002296795477 with transfer_delete_source=1 left ON in the live DB.

## Live transfer progress badge (round 13)
- **Root-cause fix (queue_manager.py::_handle_transfer):** `JobRepo.fetch()` returns payload as raw JSON TEXT — both the pre-loop write AND the in-loop write did `{**job_row['payload']}` on a str → `TypeError: 'str' object is not a mapping` → job crash loop → circuit breaker opened after ~5 crashes (`no available backend within timeout`). Fix: parse once (`cur_payload`) before the pre-loop write; both progress writes spread the dict. Lesson: whenever spreading a fetched row's payload, isinstance-str/json.loads FIRST (same bug class as models returning TEXT).
- **Endpoint** GET /api/v1/queue/transfer-progress (admin): transfer jobs running/pending/retry + **done within last 3s** (finished_at >= now-3) — tiny FakeTG transfers finish in ~60-100ms and would otherwise blink past every 2.5s poll; done jobs report pct=100. Pending jobs without progress get bytes_total/parts_total/file_name from FileRepo.get + parts. Params in fetch_all: positional tuple `(now() - 3.0,)` (JSONB-branch style).
- **Panel (App.vue):** #transfer-live badge in the files toolbar (count • max-pct, pulsing dot) fed by pollTransferProgress every 2.5s while tab==='files' (watch immediate + onBeforeUnmount cleanup). bulkTransferDo now triggers an immediate pollTransferProgress after enqueueing. JobRepo gained update_payload(job_id, payload).
- **Bonus bugfix:** bulkMode was NEVER reachable — commit b6b6bb3 added the action bar gated on bulkMode but no toggle ever set it true. New «انتخاب گروهی» toolbar button (toggleBulkMode) turns the multi-select bars on; e2e now drives the real UI path.
- **e2e gotchas:** (a) rows are name-keyed — use a per-run unique name tag, otherwise stale same-name leftovers from a crashed run get selected and the transfer skips ALL of them as "already in target" (audit_log said '0 enqueued, 3 skipped'); (b) assert the toast says «3 فایل به صف انتقال رفت» to catch silent skips; (c) refresh the table before selecting (panel table was rendered before the uploads landed). Test: upload 3×8MB → ready-wait → UI bulk-select → dialog @e2e-tprog-chan → badge visible with % → disappears when all done.
- Tests: tests/test_transfer_progress.py (1) → 161 pytest + 19 e2e; bundle index-DhYBYYtW.js. Live: 4MB transfer streamed 0→100% in payload + endpoint, job done, file moved to -1002296795477; post-fix live re-test done.

## Per-job resume from upload-session offset (round 17)
- **Feature:** `POST /api/v1/queue/resume/{job_id}` resumes a durable upload job (status `pending`/`running`/`retry`, not paused) from the server's stored upload-session offset (`upload_sessions.offset`). Non-upload or paused jobs return 409.
- **Handler (`_handle_upload`):** when a `session_id` is present, the upload streams from the session's saved offset instead of re-sending the full tmp file; on success the session `offset` is advanced to the file size so the offset never rewinds. Push-button `qResume` by kind still exists too.
- **Panel:** the queue table now shows a "Continue from offset N" button for active upload jobs (pending/running/retry), not just `failed`;"`qResumeJob(j)` calls the per-job endpoint.
- **Tests:** `tests/test_queue.py` + `tests/test_api.py` full green (22 dots; also passed the earlier janitor suite + the whole suite `EXIT:0`).

## Manual admin janitor trigger (round 17)
- **Endpoint:** `POST /api/v1/admin/janitor/run` (admin auth). Runs one janitor sweep immediately: deletes stale tmp files >1h in `final_tmp_dir()`, purges expired upload sessions + their `{session}.part` tombstoned tmp files, old finished jobs (>24h), revoked keys (>30d), audit_log/revoked_tokens (>90d). Returns per-category counts (`tmp_removed`, `sessions_deleted`, `jobs_purged`, `trash_purged`, `revoked_keys_deleted`, `audit_deleted`, `revoked_tokens_deleted`).
- **Why:** the janitor loop is 600s (`app/services/janitor.py`), so a forced/urgent cleanup (e.g. in-flight crash cleanup, stale session expiry) needs a manual trigger rather than waiting.
- **Tests:** `tests/test_janitor_admin.py` (3: deletes stale tmp files + expired sessions, rejects unauthenticated 401, rejects uninitialized janitor 503). Full suite green.

## Sidebar drag & drop (round 12)
- Sidebar rows (root «همه فایل‌ها» + each folder) accept drops: onSidebarDrop enqueues via enqueueUploadJobs with the row's path as X-Folder; scoped folders also pin their own channel (folder.scope). Row highlights (dashed outline) during hover via sbRowStyle; ev.sidebarDropped flag stops section-level onFilesDrop from opening the dialog; toast reports target folder; filesFolderDraft follows the drop target.
- Gotcha: SIDEBAR_ROOT key ("__root__") distinguishes no-hover from hovering the root row (null is a valid folder id).
- Tests: e2e "drop onto a sidebar folder row…" (creates folder, hover outline, drop, folder_id check on server) → 18 e2e + 160 pytest; bundle index-DLK0rnjN.js.

### Round 18 — Jan 2026 (Admin-triggered manual janitor + upload-cancel bytes)
- `POST /api/v1/admin/janitor/run` hands one sweep immediately
  (stale tmp >1h, tombstoned sessions + `.part`, jobs >24h, revoked keys >30d,
  audit/revoked_tokens >90d); returns per-category counts.
- Tests in `tests/test_janitor_admin.py` (3 pass, red/verifying-style).
- Panel: `cancelUploadJob` now reads `bytes_freed`/`part_bytes` robustly and
  accumulates `totalFreedBytes`; `#upload-tray` heading shows a freed-space
  summary badge (`{{ totalFreedBytes > 0 ? fmtBytes(totalFreedBytes) : "۰" }}`),
  matching Janitor round-18 spec.
- Ref: Janitor class in `app/services/janitor.py` (600s loop `_loop`, `_sweep`
  with tmp>1h, sessions via `UploadSessionRepo.stale(ttl)`, jobs via
  `purge_finished(86400)`, keys/audit_tokens, >7d trash → `KIND_DELETE` w/
  `?purge=true`).### Round 18 — Jan 2026 (Admin-triggered manual janitor)
- `POST /api/v1/admin/janitor/run` hands one sweep immediately
  (stale tmp >1h, tombstoned sessions + `.part`, jobs >24h, revoked keys >30d,
  audit/revoked_tokens >90d); returns per-category counts.
- Tests in `tests/test_janitor_admin.py` (3 pass, red/verifying-style).
- Panel: `cancelUploadJob` now reads `bytes_freed`/`part_bytes` robustly and
  accumulates `totalFreedBytes`; `#upload-tray` heading shows a freed-space
  summary badge (`{{ totalFreedBytes > 0 ? fmtBytes(totalFreedBytes) : "۰" }}`).
- Ref: Janitor class in `app/services/janitor.py` (600s loop `_loop`, `_sweep`
  with tmp>1h, sessions via `UploadSessionRepo.stale(ttl)`, jobs via
  `purge_finished(86400)`, keys/audit_tokens, >7d trash → `KIND_DELETE` w/
  `?purge=true`).


## Sidebar drag & drop (round 12)
- Sidebar rows (root «همه فایل‌ها» + each folder) accept drops: onSidebarDrop enqueues via enqueueUploadJobs with the row's path as X-Folder; scoped folders also pin their own channel (folder.scope). Row highlights (dashed outline) during hover via sbRowStyle; ev.sidebarDropped flag stops section-level onFilesDrop from opening the dialog; toast reports target folder; filesFolderDraft follows the drop target.
- Gotcha: SIDEBAR_ROOT key ("__root__") distinguishes no-hover from hovering the root row (null is a valid folder id).
- Tests: e2e "drop onto a sidebar folder row…" (creates folder, hover outline, drop, folder_id check on server) → 18 e2e + 160 pytest; bundle index-DLK0rnjN.js.

### Round 18 — Jan 2026 (Admin-triggered manual janitor + upload-cancel bytes)
- `POST /api/v1/admin/janitor/run` hands one sweep immediately
  (stale tmp >1h, tombstoned sessions + `.part`, jobs >24h, revoked keys >30d,
  audit/revoked_tokens >90d); returns per-category counts.
- Tests in `tests/test_janitor_admin.py` (3 pass, red/verifying-style).
- Panel: `cancelUploadJob` now reads `bytes_freed`/`part_bytes` robustly and
  accumulates `totalFreedBytes`; `#upload-tray` heading shows a freed-space
  summary badge (`{{ totalFreedBytes > 0 ? fmtBytes(totalFreedBytes) : "۰" }}`),
  matching Janitor round-18 spec.
- Ref: Janitor class in `app/services/janitor.py` (600s loop `_loop`, `_sweep`
  with tmp>1h, sessions via `UploadSessionRepo.stale(ttl)`, jobs via
  `purge_finished(86400)`, keys/audit_tokens, >7d trash → `KIND_DELETE` w/
  `?purge=true`).


### Panel UI summary (round 18)
- Tray heading badge: `{{ totalFreedBytes > 0 ? fmtBytes(totalFreedBytes) : "۰" }}`
  under «آپلودها (N) فعال… / M، فضا آزاد شده: …».
- `cancelUploadJob` robust read: `.then(async (r) => { const jr = await r.json(); const freed = Number(jr.bytes_freed ?? jr.part_bytes?.length ?? 0) || 0; ... })`; alias `bytesToString = fmtBytes`.
- Tests `tests/test_upload_cancel.py` green (full suite EXIT:0).
