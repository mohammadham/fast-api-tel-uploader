"""One-shot health report: server, sessions, database.

Shared by GET /api/v1/admin/health-report (panel dashboard) and the
standalone scripts/healthcheck.py, so both always agree on what "healthy"
means:

- server: liveness pieces (uptime, effective fake_tg, workers, queue stats).
  ``in_app`` marks whether the report runs inside the live app; a standalone
  script run has no queue/uptime of its own (the API report is authoritative).
- sessions: every tg account + bot with a cheap decrypt check of its stored
  secret material (proves the rows are usable without contacting Telegram).
  The effective fake_tg/mode comes from the settings table overlay so a
  standalone run reports the SAME mode the real server would pick.
- database: row counts, sqlite file size + integrity (pg: counts only).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

from ..core.config import get_settings
from ..core.security import decrypt_str


async def _runtime_overlay(db) -> Dict[str, str]:
    """Runtime settings straight from the settings table (key→raw value)."""
    try:
        rows = await db.fetch_all("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}


def _apply_overlay(s, overlay: Dict[str, str]) -> None:
    """Same semantics as TGManager._settings: DB runtime values win over env."""
    if "fake_tg" in overlay:
        s.fake_tg = bool(int(overlay["fake_tg"] or 0))
    if overlay.get("download_workers"):
        s.download_workers = int(overlay["download_workers"])
    if overlay.get("upload_workers"):
        s.upload_workers = int(overlay["upload_workers"])
    if "tg_storage_chat" in overlay:
        s.tg_storage_chat = str(overlay.get("tg_storage_chat") or "")


def _decrypt_ok(encrypted: str) -> bool:
    """True when the stored secret material still decrypts under the current
    TGDRIVE_SECRET (a False here means that session/token is unrecoverable)."""
    if not encrypted:
        return False
    try:
        return bool(decrypt_str(encrypted))
    except Exception:
        return False


async def build_health_report(db, *, with_integrity: bool = True) -> Dict[str, Any]:
    overlay = await _runtime_overlay(db)
    s = get_settings()
    _apply_overlay(s, overlay)

    # ── server ────────────────────────────────────────────────────────
    from ..core.metrics import metrics
    from ..core.state import state

    in_app = state.db is not None
    qstats: Dict[str, Any] = {}
    if state.queue is not None:
        try:
            qstats = await state.queue.stats()
        except Exception:
            qstats = {}
    server = {
        "in_app": in_app,
        "ok": in_app,
        "uptime_s": round(time.time() - metrics._start, 1) if in_app else 0,
        "fake_tg": bool(s.fake_tg),
        "storage_chat": s.tg_storage_chat or "",
        "workers": {"download": s.download_workers, "upload": s.upload_workers},
        "queue": {k: qstats.get(k, 0) for k in ("pending", "running", "retry", "failed", "done")},
    }

    # ── sessions ──────────────────────────────────────────────────────
    accounts = []
    for r in await db.fetch_all(
        "SELECT id, label, phone, status, enabled, session_enc, last_error FROM tg_accounts ORDER BY id"
    ):
        accounts.append({
            "id": r["id"], "label": r["label"] or "", "phone": r["phone"] or "",
            "status": r["status"], "enabled": bool(r["enabled"]),
            "session_ok": _decrypt_ok(r["session_enc"]),
            "last_error": (r["last_error"] or "")[:160],
        })
    bots = []
    for r in await db.fetch_all(
        "SELECT id, label, status, enabled, token_enc, last_error FROM bot_tokens ORDER BY id"
    ):
        bots.append({
            "id": r["id"], "label": r["label"] or "",
            "status": r["status"], "enabled": bool(r["enabled"]),
            "token_ok": _decrypt_ok(r["token_enc"]),
            "last_error": (r["last_error"] or "")[:160],
        })
    sessions = {
        "accounts": accounts,
        "accounts_ok": sum(1 for a in accounts if a["enabled"] and a["status"] == "ready" and a["session_ok"]),
        "bots": bots,
        "bots_ok": sum(1 for b in bots if b["enabled"] and b["status"] == "ready" and b["token_ok"]),
        "all_ok": all(a["session_ok"] for a in accounts) and all(b["token_ok"] for b in bots),
    }

    # ── database ──────────────────────────────────────────────────────
    is_sqlite = getattr(db, "is_sqlite", False)
    files_total = await db.scalar("SELECT COUNT(*) FROM files") or 0
    files_ready = await db.scalar("SELECT COUNT(*) FROM files WHERE status='ready'") or 0
    bytes_stored = await db.scalar("SELECT COALESCE(SUM(size),0) FROM files WHERE status='ready'") or 0
    trashed = await db.scalar("SELECT COUNT(*) FROM files WHERE deleted_at IS NOT NULL") or 0
    jobs_failed = await db.scalar("SELECT COUNT(*) FROM jobs WHERE status='failed'") or 0
    db_path = getattr(db, "path", "") if is_sqlite else ""
    database: Dict[str, Any] = {
        "engine": "sqlite" if is_sqlite else "postgres",
        "path": db_path,
        "size_bytes": os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0,
        "files": {"total": files_total, "ready": files_ready, "bytes": bytes_stored, "trashed": trashed},
        "folders": await db.scalar("SELECT COUNT(*) FROM folders") or 0,
        "api_keys_active": await db.scalar("SELECT COUNT(*) FROM api_keys WHERE revoked=0") or 0,
        "channels": await db.scalar("SELECT COUNT(*) FROM channels") or 0,
        "jobs_failed": jobs_failed,
        "integrity": "unknown",
    }
    if is_sqlite and with_integrity:
        try:
            row = await db.fetch_one("PRAGMA quick_check")
            # fetch_one returns a column-name-keyed dict; PRAGMA's column is "quick_check"
            database["integrity"] = (list(row.values())[0] if row else "unknown")
        except Exception as exc:
            database["integrity"] = f"error: {exc}"[:80]

    return {"ts": time.time(), "server": server, "sessions": sessions, "database": database}
