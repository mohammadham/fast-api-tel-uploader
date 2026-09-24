"""Telegram proxy pool tests (SQLite mode)."""
from __future__ import annotations

import pytest

from app.core.state import get_db
from app.services.proxy_service import parse_share_link, build_telethon_proxy


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_parse_tg_proxy_link():
    p = parse_share_link("tg://proxy?server=1.2.3.4&port=443&secret=dd" + "ab" * 16)
    assert p["kind"] == "mtproto" and p["host"] == "1.2.3.4" and p["port"] == 443
    assert p["secret_hex"].startswith("dd")


def test_parse_tme_proxy_link():
    p = parse_share_link("https://t.me/proxy?server=5.6.7.8&port=8888&secret=ee" + "cd" * 16)
    assert p["host"] == "5.6.7.8" and p["secret_hex"].startswith("ee")


def test_parse_socks_url_and_bare():
    p = parse_share_link("socks5://user:pass@9.9.9.9:1080")
    assert p == {"kind": "socks5", "host": "9.9.9.9", "port": 1080, "secret_hex": "", "username": "user", "password": "pass"}
    assert parse_share_link("10.0.0.1:8080")["port"] == 8080
    assert parse_share_link("10.0.0.1:1080:usr:pwd")["username"] == "usr"


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_share_link("")
    with pytest.raises(ValueError):
        parse_share_link("ftp://x:y")
    with pytest.raises(ValueError):
        parse_share_link("not a proxy")


def test_build_telethon_mtproto_arg():
    arg = build_telethon_proxy({"kind": "mtproto", "host": "1.2.3.4", "port": 443, "secret_hex": "dd" + "ab" * 16})
    assert arg is not None and arg[1] == "1.2.3.4" and arg[2] == 443


def test_build_telethon_socks5_arg():
    arg = build_telethon_proxy({"kind": "socks5", "host": "1.2.3.4", "port": 1080, "username": "u", "_password_plain": "p"})
    assert arg is not None and arg[0] == 1 and arg[4] == "u" and arg[5] == "p"


async def test_proxy_crud_flow(client, admin_headers):
    # add via link
    r = await client.post(
        "/api/v1/admin/proxies",
        json={"link": "tg://proxy?server=1.2.3.4&port=443&secret=" + "ab" * 16, "label": "م/پ ۱"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # add via manual socks5
    r2 = await client.post(
        "/api/v1/admin/proxies",
        json={"host": "127.0.0.1", "port": 1080, "kind": "socks5", "label": "local"},
        headers=admin_headers,
    )
    assert r2.status_code == 200
    # list
    lst = (await client.get("/api/v1/admin/proxies", headers=admin_headers)).json()["items"]
    assert len(lst) == 2 and lst[0]["label"] == "م/پ ۱"
    # toggle off
    r = await client.patch(f"/api/v1/admin/proxies/{pid}", json={"enabled": False}, headers=admin_headers)
    assert r.status_code == 200
    lst = (await client.get("/api/v1/admin/proxies", headers=admin_headers)).json()["items"]
    row = next(x for x in lst if x["id"] == pid)
    assert row["enabled"] == 0
    # delete
    r = await client.delete(f"/api/v1/admin/proxies/{pid}", headers=admin_headers)
    assert r.status_code == 200
    lst = (await client.get("/api/v1/admin/proxies", headers=admin_headers)).json()["items"]
    assert len(lst) == 1


async def test_proxy_test_endpoint_down_host(client, admin_headers):
    # loopback closed port → connection refused → status down (deterministic,
    # unaffected by VPN/TUN routing that swallows unroutable IPs)
    r = await client.post(
        "/api/v1/admin/proxies",
        json={"host": "127.0.0.1", "port": 1, "kind": "socks5"},
        headers=admin_headers,
    )
    pid = r.json()["id"]
    r = await client.post(f"/api/v1/admin/proxies/{pid}/test", headers=admin_headers)
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["status"] == "down" and item["latency_ms"] == -1
    # cleanup
    await client.delete(f"/api/v1/admin/proxies/{pid}", headers=admin_headers)


async def test_proxy_sorting_by_latency(client, admin_headers):
    db = await get_db()
    from app.core.models import ProxyRepo

    repo = ProxyRepo(db)
    await repo.create(host="1.1.1.1", port=443, kind="socks5", label="fast")
    await repo.create(host="2.2.2.2", port=443, kind="socks5", label="slow")
    await repo.create(host="3.3.3.3", port=443, kind="socks5", label="never-tested")
    await repo.set_check_result(1, "ok", 120.0)
    await repo.set_check_result(2, "ok", 950.0)
    rows = await repo.list()
    # tested ascending first, then untested
    labels = [r["label"] for r in rows]
    assert labels == ["fast", "slow", "never-tested"]
    for r in rows:
        await repo.delete(r["id"])


async def test_proxy_enabled_setting_controls_selection(client, admin_headers):
    db = await get_db()
    from app.core.models import ProxyRepo
    from app.core.settings_service import save_runtime_settings
    from app.services import proxy_service

    await save_runtime_settings(db, {"proxy_enabled": 1}, actor="test")
    proxy_service.selector.invalidate()
    # no proxies → None
    assert await proxy_service.get_active_proxy(db) is None
    # add one → selected
    repo = ProxyRepo(db)
    await repo.create(host="127.0.0.1", port=1, kind="socks5", label="x")
    try:
        got = await proxy_service.get_active_proxy(db)
        assert got is not None and got["host"] == "127.0.0.1"
        # disable globally → None
        await save_runtime_settings(db, {"proxy_enabled": 0}, actor="test")
        proxy_service.selector.invalidate()
        assert await proxy_service.get_active_proxy(db) is None
    finally:
        for r in await repo.list():
            await repo.delete(r["id"])
        await save_runtime_settings(db, {"proxy_enabled": 0}, actor="test")
