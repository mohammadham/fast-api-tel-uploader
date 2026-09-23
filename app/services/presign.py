"""Presigned download links with HMAC, expiry and optional one-time tokens."""
from __future__ import annotations

import secrets
import time
from typing import Optional, Set

from ..core.security import sign_download, verify_download

# in-memory one-time token ledger (jti → expires_at); good enough per-process
_used: Set[str] = set()
_exp: dict[str, float] = {}


def make_link(file_id: str, *, base_url: str = "", ttl: int = 3600, one_time: bool = False) -> dict:
    from ..core.config import get_settings

    s = get_settings()
    exp = int(time.time() + (ttl or s.presigned_ttl))
    sig = sign_download(file_id, exp, one_time)
    jti = secrets.token_hex(8) if one_time else ""
    base = base_url.rstrip("/") or f"http://localhost:{8000}"
    url = f"{base}/d/{file_id}?exp={exp}&ot={1 if one_time else 0}&sig={sig}"
    if jti:
        url += f"&jti={jti}"
        _exp[jti] = exp
    return {"url": url, "expires_at": exp, "one_time": bool(one_time), "jti": jti}


def check(file_id: str, exp: str, ot: str, sig: str, jti: str = "") -> tuple[bool, str]:
    if not verify_download(file_id, exp, ot, sig):
        return False, "invalid or expired signature"
    if ot == "1":
        if not jti or jti in _used:
            return False, "one-time link already used"
        if _exp.get(jti, 0) < time.time():
            return False, "one-time link expired"
        _used.add(jti)
        _gc()
    return True, ""


def _gc() -> None:
    now = time.time()
    if len(_exp) > 4096:
        for jti, exp in list(_exp.items()):
            if exp < now:
                _exp.pop(jti, None)
                _used.discard(jti)
