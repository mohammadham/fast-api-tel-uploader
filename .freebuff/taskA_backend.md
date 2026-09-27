# Task A (backend) — TelegramDrive — ✅ ALL ITEMS DONE (do not re-dispatch)

> **Status 2026-09-27:** A1, A2, A3, A5, A6, A7 landed in commits `c83e2ae`, `35d83aa`;
> A4 in `8ff0705`; A8 in `99aa238`. Suite is green: **pytest 125 passed**, **playwright 10 passed**.
> Do NOT re-run these tasks. Anything genuinely new goes into a fresh bundle.

## What landed (reference only — already committed)

- **A1** `app/tg/bot_service.py::_reply` — absolute URL + `log.warning` instead of silent `except: pass`.
- **A2** `app/core/models.py::FileRepo.list/count` — CTE `folder_id` is bound as the FIRST param, so
  `q`/`mime` filters can no longer shift placeholders on recursive listings; folder endpoints now take
  `mime`/`order` and return page-independent `total`; regression tests in `tests/test_folders.py`
  (`test_folder_recursive_search_and_total`, `test_batch_move_files_endpoint`).
- **A3** `app/queue/queue_manager.py` — atomic claim via conditional `UPDATE ... WHERE status IN ('pending','retry')`
  + rows-affected check.
- **A4** `tests/test_api.py` — `test_files_q_and_pagination_total`, `test_blocked_files_report`.
- **A5** `trusted_proxies` setting + `get_client_ip` XFF validation (`ipaddress`), documented in `.env.example`.
- **A6** indexes `idx_files_storage_chat_created`, `idx_files_folder_created`, `idx_files_created`,
  `idx_jobs_status_nextrun` in BOTH the SQLite and PG schema sections of `app/core/db.py`.
- **A7** 30s TTL cache for `/admin/storage-channels` with invalidation hooks in `keys.py` + `models.py`.
- **A8** `POST /api/v1/files/{id}/preview-token` — HMAC `ptk_<file>:<exp>:<sig>`, TTL clamped 10..600s,
  file-bound, accepted only on that file's `/preview` path (`tests/test_api.py::test_preview_token_flow`).
  Panel `filePreview` now mints a token per open instead of embedding the JWT in media-tag URLs.

## Residual notes (small, optional, NOT urgent)

- `panel-parity.yml` CI guard greps `${` over the whole App.vue (30 hits, all in `<script setup>`); the
  template itself is clean. Narrow the guard to the template block or it will stay red forever.
- E2E relies on a persistent `data-e2e/telegramdrive.db`; if the storage-channels test ever fails with a
  count mismatch, purge stale rows (`files`, `revoked api_keys`, `jobs`) — tests now self-heal but the DB
  can still carry pre-fix residue.

## If you get new backend work

Rules unchanged: `.venv/Scripts/python.exe -m pytest tests/ -q` must stay green; backend files only
(`app/**`, `tests/**`); no `panel/**`, `static/**`, `e2e/**`, `data-*/**`; no git commit; no `.env`/secret
changes; run the relevant test file after each change and the full suite at the end.
