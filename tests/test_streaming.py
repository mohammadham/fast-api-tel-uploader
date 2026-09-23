"""Range parsing + multipart streaming logic tests."""
from __future__ import annotations

import pytest

from app.services.streaming import parse_range


def test_full_none():
    assert parse_range("bytes=0-", 100) == (0, 99)
    assert parse_range("bytes=", 100) is None


def test_partial():
    assert parse_range("bytes=10-19", 100) == (10, 19)
    assert parse_range("bytes=90-", 100) == (90, 99)


def test_suffix():
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=-500", 100) == (0, 99)


def test_end_clamped():
    assert parse_range("bytes=95-500", 100) == (95, 99)


def test_unsatisfiable():
    with pytest.raises(ValueError):
        parse_range("bytes=100-", 100)
    with pytest.raises(ValueError):
        parse_range("bytes=50-49", 100)


def test_invalid_header():
    assert parse_range("items=0-5", 100) is None
