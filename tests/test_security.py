"""Unit tests for security primitives and rate limiting."""
from __future__ import annotations

import time

from app.core.rate_limit import TokenBucket
from app.core.security import (
    create_token,
    decode_token,
    decrypt_str,
    encrypt_str,
    generate_api_key,
    hash_password,
    key_hash,
    sign_download,
    verify_download,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_jwt_kinds():
    tok = create_token("admin", "access", 60)
    assert decode_token(tok, "access")["sub"] == "admin"
    assert decode_token(tok, "refresh") is None  # wrong kind
    assert decode_token(tok + "x", "access") is None  # tampered


def test_api_key_format():
    k = generate_api_key()
    assert k.startswith("td_") and len(k) == 46
    assert key_hash(k) != k


def test_fernet_roundtrip():
    c = encrypt_str("session-string-XYZ")
    assert c != "session-string-XYZ"
    assert decrypt_str(c) == "session-string-XYZ"


def test_presign_flow():
    fid = "f_abc"
    exp = int(time.time()) + 300
    sig = sign_download(fid, exp, False)
    assert verify_download(fid, str(exp), "0", sig)
    assert not verify_download(fid, str(exp - 400), "0", sig)  # expired
    assert not verify_download("f_other", str(exp), "0", sig)
    assert not verify_download(fid, str(exp), "1", sig)  # one_time flag mismatch


def test_token_bucket_refill():
    b = TokenBucket(capacity=2, refill_per_sec=10)
    assert b.try_take() and b.try_take()
    assert not b.try_take()
    time.sleep(0.15)
    assert b.try_take()  # refilled ~1.5 tokens
