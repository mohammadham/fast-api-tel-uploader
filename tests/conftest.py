"""Shared fixtures: app with FakeTelegram backend + tmp SQLite database."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TGDRIVE_FAKE_TG", "1")
os.environ.setdefault("TGDRIVE_SECRET", "test-secret-please-change-me-32chars")
os.environ.setdefault("TGDRIVE_ADMIN_USERNAME", "admin")
os.environ.setdefault("TGDRIVE_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("TGDRIVE_DOWNLOAD_WORKERS", "2")
os.environ.setdefault("TGDRIVE_UPLOAD_WORKERS", "1")


@pytest.fixture()
async def client():
    from asgi_lifespan import LifespanManager
    from httpx import AsyncClient, ASGITransport

    from app.core.config import get_settings
    from app.core.state import state
    from app.main import app

    # isolate state per test
    state.db = None
    state.manager = None
    state.queue = None
    state.bots = None
    state.janitor = None

    s = get_settings()
    tmpdir = tempfile.mkdtemp(prefix="tgdrive-test-")
    s.data_dir = tmpdir
    s.db_path = os.path.join(tmpdir, "test.sqlite3")
    s.upload_tmp_dir = os.path.join(tmpdir, "tmp")

    get_settings.cache_clear()
    # NOTE: after cache_clear, rebuild the same values into the fresh instance
    fresh = get_settings()
    fresh.fake_tg = True
    fresh.secret = os.environ["TGDRIVE_SECRET"]
    fresh.admin_username = "admin"
    fresh.admin_password = "admin123"
    fresh.data_dir = tmpdir
    fresh.db_path = os.path.join(tmpdir, "test.sqlite3")
    fresh.upload_tmp_dir = os.path.join(tmpdir, "tmp")

    async with LifespanManager(app):
        # ensure one healthy fake backend exists for every test
        from app.core.models import AccountRepo
        from app.core.security import encrypt_str

        acc_id = await AccountRepo(state.db).create("test-acc", "+989120000000", "")
        await AccountRepo(state.db).set_session(acc_id, encrypt_str("fake-session::test"), "ready")
        await state.manager.refresh_one_account(acc_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    from app.tg.fake import FakeBackend

    FakeBackend.STORE.clear()
    from app.core.rate_limit import limiter, quota

    limiter._buckets.clear()
    quota._bytes.clear()
    quota._day.clear()
    # reset singletons
    state.db = None
    state.manager = None
    state.queue = None
    state.bots = None
    state.janitor = None


@pytest.fixture()
async def token(client):
    resp = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
async def api_key(client, token):
    resp = await client.post(
        "/api/v1/keys",
        json={"name": "test-key", "scopes": "read,write", "rpm": 600},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["key"]


AUTH = {"Authorization": "Bearer x"}  # replaced per-test with real key
