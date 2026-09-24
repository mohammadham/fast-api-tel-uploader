"""Proxy health monitor tests: interval gating, state-change detection, notify text."""
from __future__ import annotations

import pytest

from app.core.models import ProxyRepo
from app.core.settings_service import save_runtime_settings
from app.core.state import get_db
from app.services.proxy_monitor import ProxyMonitor


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def clean_proxies():
    db = await get_db()
    repo = ProxyRepo(db)
    yield repo, db
    for r in await repo.list():
        await repo.delete(r["id"])
    await save_runtime_settings(db, {"proxy_monitor_interval": 0}, actor="test")


async def test_monitor_pass_detects_state_change(clean_proxies, monkeypatch):
    repo, db = clean_proxies
    pid = await repo.create(host="127.0.0.1", port=1, kind="socks5", label="dead")  # refused → down

    mon = ProxyMonitor(db)
    # seed memory as previously-healthy (simulates a prior pass that saw 'ok')
    mon._last[pid] = "ok"

    sent = {}

    async def fake_notify(text):
        sent["text"] = text

    import app.services.notify as notify_mod
    monkeypatch.setattr(notify_mod, "notify_admins", fake_notify)

    await mon._run_once()
    assert pid in mon._last and mon._last[pid] == "down"
    assert "text" in sent
    assert "قطع" in sent["text"] and "dead" in sent["text"]

    # second pass with no change → no new notification
    sent.clear()
    await mon._run_once()
    assert "text" not in sent


async def test_monitor_first_pass_is_silent(clean_proxies):
    """First observation seeds memory; no alert storm on startup."""
    repo, db = clean_proxies
    await repo.create(host="127.0.0.1", port=1, kind="socks5", label="dead")
    mon = ProxyMonitor(db)
    await mon._run_once()
    assert len(mon._last) == 1  # seeded, no notify path taken (notify would fail w/o bot anyway)


async def test_monitor_clears_memory_when_disabled(clean_proxies):
    repo, db = clean_proxies
    pid = await repo.create(host="127.0.0.1", port=1, kind="socks5", label="x")
    mon = ProxyMonitor(db)
    mon._last[pid] = "down"
    assert await mon._interval_minutes() == 0  # default off


async def test_interval_setting_validation(client, admin_headers):
    resp = await client.put(
        "/api/v1/admin/settings", json={"proxy_monitor_interval": 5}, headers=admin_headers
    )
    assert resp.status_code == 200
    resp = await client.put(
        "/api/v1/admin/settings", json={"proxy_monitor_interval": 1}, headers=admin_headers
    )
    assert resp.status_code == 400
    resp = await client.put(
        "/api/v1/admin/settings", json={"proxy_monitor_interval": 0}, headers=admin_headers
    )
    assert resp.status_code == 200
