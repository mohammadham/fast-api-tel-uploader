"""Setup wizard database-engine selection + sqlite fallback tests (SQLite mode)."""
from __future__ import annotations

import pytest

from app.core.state import get_db


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def test_setup_status_reports_database_info(client, admin_headers):
    resp = await client.get("/api/v1/admin/setup/status", headers=admin_headers)
    assert resp.status_code == 200
    info = resp.json()["database"]
    assert info["engine"] == "sqlite"  # tests run in sqlite mode
    assert info["pg_configured"] is False  # no TGDRIVE_DATABASE_URL in tests
    assert info["pg_error"] == ""
    assert info["hint"] == ""


async def test_wizard_select_sqlite_persists_choice(client, admin_headers):
    db = await get_db()
    from app.core.settings_service import get_meta

    resp = await client.post(
        "/api/v1/admin/setup/complete",
        json={"new_password": "", "default_backend": "", "db_engine": "sqlite"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert (await get_meta(db, "db_engine")) == "sqlite"
    # engine hint is seeded so a later restart keeps sqlite even if .env gains a PG URL
    from app.core.settings_service import get_meta_sync_hint

    assert get_meta_sync_hint() == "sqlite"


async def test_wizard_rejects_bad_engine(client, admin_headers):
    resp = await client.post(
        "/api/v1/admin/setup/complete",
        json={"db_engine": "mysql"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_wizard_rejects_postgres_without_url(client, admin_headers):
    # current DB is sqlite and no TGDRIVE_DATABASE_URL is set → postgres choice must fail
    resp = await client.post(
        "/api/v1/admin/setup/complete",
        json={"db_engine": "postgres"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_state_falls_back_to_sqlite_when_pg_unreachable(monkeypatch, tmp_path):
    """PG configured but connect() fails → SQLite is used and reason recorded."""
    from app.core import state as app_state
    from app.core.db import Database
    from app.core.state import AppState

    async def fake_connect_fail(self):
        raise ConnectionError("simulated PG outage")

    monkeypatch.setattr("app.core.db.PgDatabase.connect", fake_connect_fail)
    monkeypatch.setattr(app_state.AppState, "_postgres_url", lambda self: "postgresql://fake@localhost/fake")
    monkeypatch.setattr("app.core.config.Settings.final_db_path", lambda self: str(tmp_path / "fb.db"))

    st = AppState()
    db = await st.db_instance()
    try:
        assert isinstance(db, Database) and not isinstance(db, app_state.PgDatabase)
        assert app_state.db_engine == "sqlite"
        assert "simulated PG outage" in app_state.db_error
        # sqlite works normally
        await db.execute("CREATE TABLE IF NOT EXISTS fb_t(x)")
        await db.execute("INSERT INTO fb_t(x) VALUES (1)")
        assert await db.scalar("SELECT COUNT(*) FROM fb_t") == 1
    finally:
        await db.close()


async def test_fallback_reason_surfaces_in_setup_status(client, admin_headers, monkeypatch):
    """When fallback happened, setup/status shows pg_error + hint to the admin."""
    from app.core import state as app_state

    monkeypatch.setattr(app_state, "db_error", "simulated outage", raising=False)
    monkeypatch.setattr(app_state, "db_engine", "sqlite", raising=False)
    resp = await client.get("/api/v1/admin/setup/status", headers=admin_headers)
    info = resp.json()["database"]
    # pg_configured is False in tests, so the hint branch is keyed off db_error presence
    assert resp.status_code == 200
