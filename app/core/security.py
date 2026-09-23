"""Security primitives: password hashing, JWT, API keys, Fernet encryption, presigned links."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

import jwt
from cryptography.fernet import Fernet

from .config import get_settings


# ---------- passwords (scrypt via hashlib — stdlib, no native deps) ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------- JWT (panel) ----------
def create_token(sub: str, kind: str, expires_in: int) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {"sub": sub, "kind": kind, "iat": now, "exp": now + expires_in, "jti": secrets.token_hex(8)}
    return jwt.encode(payload, s.secret, algorithm="HS256")


def decode_token(token: str, kind: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, get_settings().secret, algorithms=["HS256"])
        if payload.get("kind") != kind:
            return None
        return payload
    except Exception:
        return None


# ---------- refresh token blacklist (logout/compromise response) ----------
async def blacklist_refresh(db, token: str) -> None:
    from .models import now

    ttl = get_settings().refresh_ttl_days * 86400
    await db.execute(
        "INSERT OR IGNORE INTO revoked_tokens(jti, expires_at) VALUES(?,?)",
        (_token_jti(token), now() + ttl),
    )


async def refresh_blacklisted(db, token: str) -> bool:
    from .models import now

    jti = _token_jti(token)
    if not jti:
        return True
    val = await db.scalar("SELECT 1 FROM revoked_tokens WHERE jti=?", (jti,))
    if val:
        return True
    # lazily drop expired rows
    await db.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (now(),))
    return False


def _token_jti(token: str) -> str:
    try:
        payload = jwt.decode(token, get_settings().secret, algorithms=["HS256"])
        return str(payload.get("jti", ""))
    except Exception:
        return ""


# ---------- API keys ----------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_api_key() -> str:
    return "td_" + "".join(secrets.choice(_B58) for _ in range(43))


def key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def key_prefix(raw: str) -> str:
    return raw[:9]


# ---------- symmetric encryption (telegram sessions / bot tokens) ----------
def _fernet() -> Fernet:
    s = get_settings()
    if s.fernet_key:
        key = s.fernet_key.encode()
    else:
        key = hashlib.sha256(s.secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_str(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_str(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


# ---------- presigned download links ----------
def sign_download(file_id: str, expires_at: int, one_time: bool = False) -> str:
    s = get_settings()
    msg = f"{file_id}:{expires_at}:{1 if one_time else 0}"
    sig = hmac.new(s.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return sig


def verify_download(file_id: str, expires_at: str, one_time: str, sig: str) -> bool:
    try:
        exp = int(expires_at)
        if exp < int(time.time()):
            return False
        expect = sign_download(file_id, exp, one_time == "1")
        return hmac.compare_digest(expect, sig)
    except Exception:
        return False
