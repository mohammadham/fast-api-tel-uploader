# Task B (panel + e2e) — TelegramDrive

You are working in the repo at the CURRENT working directory (FastAPI backend + Vue 3 panel in `panel/src/App.vue`, built output committed to `static/`).

Backend tests: `.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_folders.py -q` (they must keep passing — these exist to make sure you didn't break the API contract; you will NOT modify backend files). E2E: `npx playwright test` (config auto-starts the fake-TG backend on port 8712 — never run it via `python -m playwright test`).

CRITICAL repo rules you must respect:
- `panel/src/App.vue` is ONE Vue SFC (no template-literal `${...}` delimiters inside the template — a CI guard greps for `${` and fails; use string concat or computed properties instead).
- After ANY change to `panel/src/**`, rebuild: `npm --prefix panel run build` (build output goes to `static/`; the committed `static/index.html` must reference the new hashed assets). Verify `static/index.html` changed and contains the new `index-*.js` hash.
- There is a CI workflow `.github/workflows/panel-parity.yml` that fails if `${` appears in App.vue or if static/index.html doesn't reference hashed assets. Run those greps yourself before finishing.
- In e2e files NEVER append via shell heredoc — edit the file with proper tools; after editing run `node --check e2e/panel.spec.mjs` (syntax gate) before running playwright.

Scope: ONLY `panel/src/App.vue`, `e2e/panel.spec.mjs`, and rebuilt `static/` output. Do NOT touch `app/**`, `tests/**`, `data-*/**`. Do NOT git commit.

Work strictly in this order; skip and report anything blocked.

## B1 (P0) — repair the broken appended e2e test
`e2e/panel.spec.mjs` currently FAILS TO PARSE: an appended test (line ~463, "per-key storage chat: create key with channel, upload file, verify channel display") was pasted through a shell heredoc and contains: Python-style `assert(...)`, invalid `page.click(selector, {label})` API usage, non-existent element ids (`#key-rpm`, `#key-quota`, `#key-storage-chat`, `#key-save` — check the real ids in App.vue's keys dialog), a stray `EOF` line at the end, and it sits OUTSIDE the `test.describe.serial` block.
- Remove that broken test entirely; then re-add a CLEAN equivalent test INSIDE the existing describe block, using only Playwright APIs and element ids/selectors that actually exist in App.vue (read the keys-dialog markup first). Flow: login → open keys tab → create key with a storage channel (use whatever field the real dialog offers; if the dialog has no storage-chat field, create the key, then set storage channel via the keys API from `page.evaluate(fetch(...))` with the stored JWT) → upload a small file via the UI → switch to files tab → assert the file row shows the channel → check the settings tab storage-channels card lists that channel.
- `node --check e2e/panel.spec.mjs` must pass, then `npx playwright test --grep "per-key"` must pass. Then run the FULL `npx playwright test` — all tests must pass (the config's webServer reuses an existing server locally; if port 8712 is occupied by a stale process, kill only that stale uvicorn on 8712 first).

## B2 (P1) — per-account storage chat editor
In the accounts tab (`tab === 'accounts'`), the table currently has a READ-ONLY column showing `a.storage_chat_id || 'default'` (~line 129). The backend already accepts updating it: `PUT /api/v1/accounts/{id}` with JSON body `{"storage_chat_id": "@channel"}` (empty string clears it).
- Add a small per-row action (pencil button or click-on-value) opening an inline edit (or minimal dialog) to set/clear `storage_chat_id`, saving via the accounts API already used elsewhere in the file (mirror how other account fields are PATCHed; check `app/api/accounts.py` for the exact verb/shape first).
- After save: refresh the accounts list and show a toast (reuse `showToast`).
- Show the value with `dir="ltr"` styling consistent with the existing cell.

## B3 (P1) — Trash tab + restore
Backend supports soft-delete/restore (`DELETE /api/v1/files/{id}` = trash by default; `GET /api/v1/files/{id}/restore`; the files list endpoint supports `?trashed=1` — verify exact param names in `app/api/files.py` first) but the panel has NO trash UI.
- Add a "سطل زباله" (trash) section — either a small tab or a collapsible card inside the files tab: lists trashed files (id, name, size, trashed date), with per-row restore button and a "خالی کردن سطل" (purge all) button that asks for confirmation (use `confirm()` style consistent with the file's existing patterns).
- Keep it lazy: fetch trashed list only when the section is opened.

## B4 (P2) — share links UI
The backend endpoints exist but have NO UI (see `app/api/files.py` lines ~341–417): create link (`POST /files/{id}/links` with slug/password/max_downloads), list (`GET /files/{id}/links`), patch (enable/disable, max_downloads), delete. The public download route is `/d/{slug}` (check the actual path in `app/api/files.py` or `app/main.py`).
- In the files tab, add a "لینک‌ها" per-row action opening a dialog: list existing links (slug, hits/max, disabled, created), create form (optional password + max downloads), toggle enable/disable, delete, and a copy-to-clipboard of the full public URL (`location.origin + /d/ + slug`).
- Keep the dialog minimal and consistent with existing dialogs in the file.

## B5 (P2) — lazy-load tab data + cache
`loadAll()` currently fires ~12 admin endpoints on every tab switch. Refactor so:
- Each loader runs on first activation of its tab and after explicit mutations of related data (existing behavior of individual loaders like `loaders.files` should be preserved where the UI relies on fresh data).
- Keep `loadAll()` for the initial dashboard load, but make subsequent `switchTab` calls only fetch that tab's data if it's stale (>10s TTL) or not yet loaded.
- Do not change any API call signatures. All tabs must still show data (the existing e2e test "tabs render real data" must keep passing).

## B6 (P3, only if B1–B5 done and green) — parallel chunked upload
Panel upload at `panel/src/App.vue` (~line 1685) sends 8MB chunks SEQUENTIALLY via fetch to `/api/v1/files/upload/session` + `PATCH /api/v1/files/upload/session/{session_id}`.
- Read the backend contract first (`app/api/files.py` `upload_chunk`) to confirm whether out-of-order chunks are supported (it advances `offset` sequentially — if the endpoint requires strictly sequential offsets, implement LIMITED pipelining instead: keep ≤2 chunks in flight where the next request only starts after the previous one returned, i.e. avoid waiting for the UI to re-render between chunks, and set `keepalive`/reuse a single fetch with same headers).
- If truly sequential-only: leave offsets sequential but add (a) retry per chunk (3 attempts, short backoff), (b) a clear per-chunk error state, (c) keep the existing progress/speed display intact.
- Do not break the existing e2e "upload via UI queues a file and lists it" test.

## Reporting
End with a compact report: per-step status, files changed, `npm --prefix panel run build` result, `node --check` result, full `npx playwright test` summary (X passed), and confirmation that `${` does NOT appear in panel/src/App.vue.
