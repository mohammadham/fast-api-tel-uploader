"""Runtime-editable settings stored in the `settings` table.

Priority: DB value > env/config default. Env values remain the fallback, so
existing .env deployments keep working; values saved from the panel win.
Sensitive keys (secrets, paths, URLs) stay env-only and are not editable here.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

# ── whitelist: key → (type, validator, description) ─────────────
def _positive_int(v: int) -> str:
    return "" if v > 0 else "must be a positive integer"


def _non_negative_int(v: int) -> str:
    return "" if v >= 0 else "must be >= 0"


def _gt_one_int(v: int) -> str:
    return "" if v >= 1 else "must be >= 1"


def _str_ok(v: str) -> str:
    return ""


def _backend_ok(v: str) -> str:
    return "" if v in ("telegram", "eitaa") else "must be 'telegram' or 'eitaa'"


EDITABLE_SETTINGS: dict[str, tuple[type, Any, str]] = {
    # limits
    "max_upload_size": (int, _positive_int, "max upload size (bytes)"),
    "split_threshold": (int, _positive_int, "split threshold (bytes)"),
    "default_key_rpm": (int, _positive_int, "default RPM for new API keys"),
    "default_key_daily_quota": (int, _non_negative_int, "default daily quota (bytes)"),
    "blocked_extensions": (str, _str_ok, "blocked extensions (comma separated)"),
    # links & expiry
    "presigned_ttl": (int, _positive_int, "presigned link TTL (seconds)"),
    "upload_session_ttl_minutes": (int, _positive_int, "upload session TTL (minutes)"),
    "job_max_retries": (int, _non_negative_int, "max job retries"),
    # queue & concurrency
    "download_workers": (int, _gt_one_int, "download workers (applied live)"),
    "upload_workers": (int, _gt_one_int, "upload workers (applied live)"),
    "max_concurrent_downloads": (int, _gt_one_int, "max concurrent downloads per account"),
    "max_concurrent_uploads": (int, _gt_one_int, "max concurrent uploads"),
    # backend
    "default_backend": (str, _backend_ok, "default storage backend"),
}

_SETTING_GROUPS: dict[str, list[str]] = {
    "limits": [
        "max_upload_size",
        "split_threshold",
        "default_key_rpm",
        "default_key_daily_quota",
        "blocked_extensions",
    ],
    "links": ["presigned_ttl", "upload_session_ttl_minutes", "job_max_retries"],
    "queue": [
        "download_workers",
        "upload_workers",
        "max_concurrent_downloads",
        "max_concurrent_uploads",
    ],
    "backend": ["default_backend"],
}

CACHE_TTL = 10.0  # seconds; cheap staleness window for multi-node convergence


class SettingsService:
    """Per-process cached view of the settings table (TTL-based refresh)."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._loaded_at: float = 0.0

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def get_all(self, db, force: bool = False) -> Dict[str, Any]:
        from .config import get_settings

        if not force and self._loaded_at and (time.time() - self._loaded_at) < CACHE_TTL:
            return self._cache
        s = get_settings()
        rows = await db.fetch_all("SELECT key, value FROM settings")
        db_vals = {r["key"]: r["value"] for r in rows}
        out: Dict[str, Any] = {}
        for key in EDITABLE_SETTINGS:
            raw = db_vals.get(key, "")
            if str(raw).strip() != "":
                vtype = EDITABLE_SETTINGS[key][0]
                try:
                    out[key] = int(raw) if vtype is int else str(raw)
                except (ValueError, TypeError):
                    out[key] = getattr(s, key, None)
            else:
                out[key] = getattr(s, key, None)
        self._cache = out
        self._loaded_at = time.time()
        return out


_service_instance: Optional[SettingsService] = None


def runtime_settings() -> SettingsService:
    global _service_instance
    if _service_instance is None:
        _service_instance = SettingsService()
    return _service_instance


async def get_runtime(db, key: str) -> Any:
    vals = await runtime_settings().get_all(db)
    return vals.get(key)


async def apply_runtime(db) -> None:
    """Push live side effects when settings change (worker resize)."""
    from .state import state

    vals = await runtime_settings().get_all(db, force=True)
    if state.queue is not None and hasattr(state.queue, "set_worker_counts"):
        try:
            state.queue.set_worker_counts(
                int(vals.get("download_workers") or 4),
                int(vals.get("upload_workers") or 2),
            )
        except Exception:
            pass


async def save_runtime_settings(db, updates: dict, actor: str = "") -> list:
    """Validate + persist; returns list of applied keys."""
    from .models import now

    applied: list = []
    for key, raw in (updates or {}).items():
        if key not in EDITABLE_SETTINGS:
            raise ValueError(f"unknown or read-only setting: {key}")
        vtype, validator, _desc = EDITABLE_SETTINGS[key]
        if vtype is int:
            try:
                value = int(raw)
            except (ValueError, TypeError):
                raise ValueError(f"{key}: integer required")
        else:
            value = str(raw)
        err = validator(value)
        if err:
            raise ValueError(f"{key}: {err}")
        await db.execute(
            "INSERT INTO settings(key, value, updated_at, updated_by) VALUES(?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, str(value), now(), actor),
        )
        applied.append(key)
    if applied:
        await db.audit(actor, "settings.update", details=json.dumps(updates)[:300])
        runtime_settings().invalidate()
        await apply_runtime(db)
    return applied


async def reset_runtime_settings(db, actor: str = "") -> None:
    await db.execute("DELETE FROM settings")
    await db.audit(actor, "settings.reset")
    runtime_settings().invalidate()
    await apply_runtime(db)


async def editable_settings_meta(db=None) -> list:
    """Metadata for the panel: key, group, type, effective value, source."""
    from .config import get_settings

    s = get_settings()
    db_vals: Dict[str, str] = {}
    if db is not None:
        rows = await db.fetch_all("SELECT key, value FROM settings")
        db_vals = {r["key"]: r["value"] for r in rows}
    out = []
    for gid, keys in _SETTING_GROUPS.items():
        for key in keys:
            vtype, _validator, desc = EDITABLE_SETTINGS[key]
            in_db = str(db_vals.get(key, "")).strip() != ""
            out.append(
                {
                    "key": key,
                    "group": gid,
                    "type": "int" if vtype is int else "str",
                    "description": desc,
                    "current": int(db_vals[key]) if in_db and vtype is int else (db_vals[key] if in_db else getattr(s, key, None)),
                    "env_value": getattr(s, key, None),
                    "db_value": (int(db_vals[key]) if vtype is int else db_vals[key]) if in_db else None,
                    "source": "db" if in_db else "env",
                    "read_only": False,
                }
            )
    return out


# ── system_meta (starter flag etc.) ──────────────────────────────
async def get_meta(db, key: str) -> str:
    row = await db.fetch_one("SELECT value FROM system_meta WHERE key=?", (key,))
    return str(row["value"]) if row else ""


async def set_meta(db, key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO system_meta(key, value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


async def mark_initialized(db) -> bool:
    """Mark system initialized (starter flag). Returns True if it flipped now."""
    if (await get_meta(db, "initialized")) == "1":
        return False
    await set_meta(db, "initialized", "1")
    await set_meta(db, "initialized_at", str(time.time()))
    return True


async def is_initialized(db) -> bool:
    return (await get_meta(db, "initialized")) == "1"
