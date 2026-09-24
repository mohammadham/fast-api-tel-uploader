"""Proxy fallback tests: active proxy reported dead → next-best selected."""
from __future__ import annotations

import pytest

from app.core.models import ProxyRepo
from app.core.settings_service import save_runtime_settings
from app.core.state import get_db
from app.services import proxy_service


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
async def proxy_env():
    db = await get_db()
    repo = ProxyRepo(db)
    await save_runtime_settings(db, {"proxy_enabled": 1, "proxy_strategy": "speed"}, actor="test")
    proxy_service.selector.invalidate()
    proxy_service.selector._dead.clear()
    yield repo, db
    for r in await repo.list():
        await repo.delete(r["id"])
    await save_runtime_settings(db, {"proxy_enabled": 0}, actor="test")
    proxy_service.selector.invalidate()
    proxy_service.selector._dead.clear()


async def test_dead_report_falls_back_to_next(proxy_env):
    repo, db = proxy_env
    fast = await repo.create(host="10.1.1.1", port=443, kind="socks5", label="fast")
    slow = await repo.create(host="10.1.1.2", port=443, kind="socks5", label="slow")
    await repo.set_check_result(fast, "ok", 50.0)
    await repo.set_check_result(slow, "ok", 300.0)
    proxy_service.selector.invalidate()

    first = await proxy_service.get_active_proxy(db)
    assert first["id"] == fast  # fastest wins initially

    # fast reported dead → next selection picks slow
    proxy_service.selector.report_dead(fast)
    second = await proxy_service.get_active_proxy(db)
    assert second is not None and second["id"] == slow


async def test_all_dead_returns_none(proxy_env):
    repo, db = proxy_env
    only = await repo.create(host="10.1.1.1", port=443, kind="socks5", label="only")
    await repo.set_check_result(only, "ok", 50.0)
    proxy_service.selector.invalidate()

    proxy_service.selector.report_dead(only)
    assert await proxy_service.get_active_proxy(db) is None


async def test_dead_ttl_expires_and_proxy_returns(proxy_env, monkeypatch):
    repo, db = proxy_env
    only = await repo.create(host="10.1.1.1", port=443, kind="socks5", label="only")
    await repo.set_check_result(only, "ok", 50.0)
    proxy_service.selector.invalidate()

    proxy_service.selector.report_dead(only)
    assert await proxy_service.get_active_proxy(db) is None

    # simulate DEAD_TTL passing
    sel = proxy_service.selector
    sel._dead[only] = sel._dead[only] - (sel.DEAD_TTL + 1)
    back = await proxy_service.get_active_proxy(db)
    assert back is not None and back["id"] == only


async def test_healthy_report_clears_dead_mark(proxy_env):
    repo, db = proxy_env
    a = await repo.create(host="10.1.1.1", port=443, kind="socks5", label="a")
    await repo.set_check_result(a, "ok", 50.0)
    proxy_service.selector.invalidate()

    proxy_service.selector.report_dead(a)
    assert await proxy_service.get_active_proxy(db) is None

    proxy_service.selector.report_healthy(a)
    back = await proxy_service.get_active_proxy(db)
    assert back is not None and back["id"] == a


async def test_rr_strategy_skips_dead(proxy_env):
    repo, db = proxy_env
    await save_runtime_settings(db, {"proxy_strategy": "rr"}, actor="test")
    a = await repo.create(host="10.1.1.1", port=443, kind="socks5", label="a")
    b = await repo.create(host="10.1.1.2", port=443, kind="socks5", label="b")
    await repo.set_check_result(a, "ok", 50.0)
    await repo.set_check_result(b, "ok", 60.0)
    proxy_service.selector.invalidate()

    proxy_service.selector.report_dead(a)
    seen = set()
    for _ in range(3):
        row = await proxy_service.get_active_proxy(db)
        if row:
            seen.add(row["id"])
    assert a not in seen and seen <= {b}
