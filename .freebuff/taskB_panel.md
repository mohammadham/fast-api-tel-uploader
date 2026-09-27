# Task B (panel) — TelegramDrive — ✅ PARTIALLY DONE, remaining items below

> **Status 2026-09-27:** worktree was repaired against HEAD (commit `c83e2ae`); App.vue build issues
> carried from `b6b6bb3` were fixed in `35d83aa` (bulk ops wired, duplicate refs removed, blocked-files
> report card repaired, per-folder search added). Panel build is green and e2e passes (10/10).
> **Do NOT redo B1 (bulk ops) or the template repairs.**

## Already done (committed — reference only)

- **B1** bulk file operations: bulk mode toggle, select-all, bulk delete/block, bulk move dialog
  (`POST /api/v1/folders/{id}/files` with `target_folder_id`, root = id 0), per-folder search input.
- Preview dialog, links dialog, channel stats card, trash/restore button — all present and covered by e2e.

## B2 — edit account storage_chat_id in UI (still open)

Accounts table (telegram + eitaa tabs): add a per-row «کانال» action opening a small dialog
(`reactive({ open:false, id:"", chat:"", msg:"" })`) that `PATCH`es
`/api/v1/accounts/{id}` (field `storage_chat`; backend already validates `@username` / numeric / empty —
`_validate_storage_chat` in `app/api/accounts.py`). After save: `showToast("کانال ذخیره‌سازی به‌روزرسانی شد")`
+ reload accounts. Keep the `dir="ltr"` input pattern used by other dialogs. Do not touch backend files.

## B3 — Trash tab (still open)

New tab «زباله‌دان» listing soft-deleted files. Backend support exists: deleted rows carry `deleted_at`,
restore is `POST /api/v1/files/{id}/restore` (button «بازیابی» already exists in the files table). Add:
a loaders entry fetching trashed items (extend `GET /api/v1/files` with `?trashed=1` ONLY if the backend
already exposes it — check `app/api/files.py::list_files` first; if it does not, SKIP the tab and report —
do not modify `app/**`), a purge button per row (`DELETE ?purge=true` with a confirm), and an
«empty trash» action only if a backend endpoint exists. Any UI element calling a nonexistent endpoint is a
bug — verify against `app/api/files.py` before wiring.

## B4 — Share-links UI `/d/{slug}` (still open)

The links dialog (`linksDlg`, opened by the «لینک‌ها» row action) already lists links, copy, toggle
enable/disable, delete. Extend it: show the full public URL as
`location.origin + "/d/" + l.slug + "/dl"` format used by the backend route (`/d/{slug}` and
`/d/{slug}/dl` live in `app/api/files.py` public router — verify exact shape by reading it, do not guess),
a per-row copy button using that shape, and show TTL/expiry if the field exists in the links payload.
No backend changes.

## Rules (unchanged)

- Only `panel/**` (and run `cd panel && npm run build` after every change — build MUST succeed;
  if `npm run build` fails with a template error, fix your template, never hand-edit `static/`).
- Never commit; never touch `app/**`, `tests/**`, `e2e/**`, `data-*/**`, `.env*`.
- After the build, run `npx playwright test` — all 10 must pass.
- Persian labels exactly as in the existing UI (مشاهده/بن/رفع بن/بازیابی/…). RTL input fields get `dir="ltr"`.
- Panel CI guard greps App.vue for `${` OUTSIDE `<script>` — never put template literals in the template;
  keep them inside `<script setup>` only.
