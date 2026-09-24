"""Dashboard proxy-health summary in /admin/overview."""
from __future__ import annotations

import pytest


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def test_overview_includes_proxy_health_empty(client, admin_headers):
    resp = await client.get("/api/v1/admin/overview", headers=admin_headers)
    assert resp.status_code == 200
    px = resp.json()["proxies"]
    assert px["total"] == 0
    assert px["alive"] == 0 and px["down"] == 0 and px["unknown"] == 0
    assert px["dead_marked"] == 0
    assert px["best_latency_ms"] == -1
    assert px["last_fallback"] is None
    assert px["fallback_count"] == 0


async def test_proxy_health_counts_alive_down(client, admin_headers):
    from app.core.models import ProxyRepo
    from app.core.state import get_db

    db = await get_db()
    repo = ProxyRepo(db)
    p1 = await repo.create(host="1.2.3.4", port=1080, kind="socks5", label="alive")
    p2 = await repo.create(host="5.6.7.8", port=1080, kind="socks5", label="dead")
    await repo.create(host="9.9.9.9", port=1080, kind="socks5", label="untested")
    await repo.set_check_result(p1, "ok", 27.5)
    await repo.set_check_result(p2, "down", -1, "refused")

    resp = await client.get("/api/v1/admin/overview", headers=admin_headers)
    px = resp.json()["proxies"]
    assert px["total"] == 3
    assert px["alive"] == 1
    assert px["down"] == 1
    assert px["unknown"] == 1
    assert px["best_latency_ms"] == 27.5


async def test_dead_mark_reflected_and_last_fallback_recorded(client, admin_headers):
    from app.services.proxy_service import selector
    from app.core.state import get_db
    from app.core.models import ProxyRepo

    db = await get_db()
    repo = ProxyRepo(db)
    pid = await repo.create(host="1.1.1.1", port=1080, kind="socks5", label="x")
    selector.report_dead(pid)
    try:
        px = (await client.get("/api/v1/admin/overview", headers=admin_headers)).json()["proxies"]
        assert px["dead_marked"] == 1
    finally:
        selector.report_healthy(pid)

    # fallback audit row (normally written by TGManager) → shows in overview
    await db.audit("system", "proxy.fallback", target=f"proxy {pid} dead via acc:1", details="next-best proxy selected")
    px = (await client.get("/api/v1/admin/overview", headers=admin_headers)).json()["proxies"]
    assert px["last_fallback"] is not None
    assert px["last_fallback"]["detail"].startswith(f"proxy {pid} dead")
    assert px["last_fallback"]["at"] > 0
