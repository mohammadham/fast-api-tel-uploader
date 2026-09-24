""".env management for the setup wizard.

Reads the effective TGDRIVE_* configuration (process env + existing .env file),
reports which bootstrap keys are missing/unset, and writes admin-provided
values back to .env atomically (with a timestamped backup) so a fresh install
can be provisioned entirely from the wizard.

Security: secret values are never returned by the read endpoint — only a
configured/missing flag and, for non-secret keys, the current value.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

log = __import__("logging").getLogger("tgdrive.env")

# bootstrap keys the wizard can provision. secret=True values are write-only:
# the API never echoes them back.
ENV_KEYS: List[dict] = [
    {"key": "TGDRIVE_SECRET", "secret": True, "required": True,
     "label": "کلید رمزنگاری (JWT/پیش‌امضا) — رشته تصادفی بلند",
     "generate": True},
    {"key": "TGDRIVE_ADMIN_USERNAME", "secret": False, "required": False,
     "label": "نام کاربری ادمین", "default": "admin"},
    {"key": "TGDRIVE_TG_API_ID", "secret": False, "required": False,
     "label": "Telegram API ID (my.telegram.org)"},
    {"key": "TGDRIVE_TG_API_HASH", "secret": True, "required": False,
     "label": "Telegram API Hash (my.telegram.org)"},
    {"key": "TGDRIVE_SESSION_STR", "secret": True, "required": False,
     "label": "Session string اکانت (برای کانال خصوصی ایتا)"},
    {"key": "TGDRIVE_EITAA_TOKEN", "secret": True, "required": False,
     "label": "توکن ایتایار"},
    {"key": "TGDRIVE_BOT_ADMIN_IDS", "secret": False, "required": False,
     "label": "آی‌دی‌های عددی ادمین‌های بات (با کاما)"},
    {"key": "TGDRIVE_DATABASE_URL", "secret": True, "required": False,
     "label": "URL پستگرس (خالی → SQLite محلی)"},
    {"key": "TGDRIVE_REDIS_URL", "secret": False, "required": False,
     "label": "URL ردیس برای صف توزیع‌شده (خالی → SQLite)"},
    {"key": "TGDRIVE_NODE_ID", "secret": False, "required": False,
     "label": "شناسه یکتای نود (چندسروره)"},
]

_ENV_PATH = Path(".env")
_LINE_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*=\s*(?P<val>.*)$")


def _parse_env_text(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            out[m.group("key")] = m.group("val").strip()
    return out


def read_env_file() -> Dict[str, str]:
    """Parse the existing .env file ({} when absent)."""
    if not _ENV_PATH.exists():
        return {}
    try:
        return _parse_env_text(_ENV_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        log.warning("cannot read .env: %s", exc)
        return {}


def _effective(key: str) -> str:
    """Process env wins over .env file (matches pydantic-settings precedence)."""
    return (os.environ.get(key) or read_env_file().get(key) or "").strip()


def env_status() -> dict:
    """Wizard-facing status: which bootstrap keys are set/missing (no secrets)."""
    file_vals = read_env_file()
    items = []
    for spec in ENV_KEYS:
        key = spec["key"]
        eff = _effective(key)
        in_file = key in file_vals and file_vals[key].strip() != ""
        from_env = bool((os.environ.get(key) or "").strip())
        item = {
            "key": key,
            "label": spec["label"],
            "required": spec["required"],
            "secret": spec["secret"],
            "configured": bool(eff),
            "source": "env" if from_env else ("file" if in_file else "missing"),
        }
        if not spec["secret"]:
            item["value"] = eff or spec.get("default", "")
        else:
            # masked preview so the admin sees *something* was configured
            item["preview"] = (eff[:4] + "…" + eff[-2:]) if len(eff) > 8 else ("•" * len(eff) if eff else "")
        items.append(item)
    return {
        "file_exists": _ENV_PATH.exists(),
        "path": str(_ENV_PATH.resolve()),
        "items": items,
        "missing_required": [i["key"] for i in items if i["required"] and not i["configured"]],
    }


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def write_env_values(values: Dict[str, str], *, actor: str = "system") -> dict:
    """Merge values into .env (creating the file if needed), atomically.

    - unknown TGDRIVE_ keys are rejected; non-TGDRIVE keys are ignored
    - existing comments/lines are preserved; only matched keys are replaced
    - a timestamped backup (.env.bak.<epoch>) is kept before the first write
    Returns {"written": [...], "skipped": [...], "created": bool}
    """
    known = {s["key"] for s in ENV_KEYS}
    clean: Dict[str, str] = {}
    for k, v in (values or {}).items():
        k = (k or "").strip().upper()
        if k not in known:
            if k.startswith("TGDRIVE_"):
                raise ValueError(f"unknown key: {k}")
            continue  # silently ignore junk
        clean[k] = str(v).replace("\n", " ").replace("\r", " ").strip()

    existing_text = ""
    created = not _ENV_PATH.exists()
    if not created:
        try:
            existing_text = _ENV_PATH.read_text(encoding="utf-8")
            bak = _ENV_PATH.with_name(f".env.bak.{int(time.time())}")
            shutil.copy2(_ENV_PATH, bak)
            try:
                os.chmod(bak, 0o600)
            except OSError:
                pass
            log.info("env backup written by %s: %s", actor, bak)
        except OSError as exc:
            log.warning("env backup failed (continuing): %s", exc)

    lines = existing_text.splitlines() if existing_text else []
    remaining = dict(clean)
    out: List[str] = []
    for line in lines:
        m = _LINE_RE.match(line.strip())
        if m and m.group("key") in remaining:
            key = m.group("key")
            val = remaining.pop(key)
            # keep trailing inline comments out of values
            out.append(f"{key}={val}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")

    text = "\n".join(out).rstrip() + "\n"
    # atomic replace: write temp in same dir, then os.replace
    fd, tmp_name = tempfile.mkstemp(dir=str(_ENV_PATH.parent), prefix=".env.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, _ENV_PATH)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.chmod(_ENV_PATH, 0o600)
    except OSError:
        pass
    log.info("env updated by %s: keys=%s created=%s", actor, sorted(clean), created)
    return {"written": sorted(clean), "skipped": [], "created": created}
