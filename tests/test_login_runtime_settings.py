"""login/start + login/complete must honor the panel's runtime settings and
actually drive Telethon — not just read env, and not silently no-op.

Regression 1: the user set real api_id/api_hash and fake_tg=0 in the panel's
settings tab, but login_start read bare get_settings() (env-only) and never
sent a code.
Regression 2: login_start/login_complete wrapped Telethon's *async* methods in
asyncio.to_thread(...), which only created the coroutine without ever running
it → instant fake "code_sent" / fake "ready" and no SMS/Telegram code.

These tests monkeypatch TelegramClient so the real-mode path is exercised
WITHOUT touching the network.
"""
from __future__ import annotations

import time

import pytest

from app.core.settings_service import runtime_settings


RUNTIME_REAL = {"fake_tg": 0, "tg_api_id": 12345, "tg_api_hash": "0123456789abcdef0123456789abcdef"}


def _set_runtime(cache_updates: dict) -> None:
    runtime_settings()._cache.update(cache_updates)


def _clear_runtime() -> None:
    for k in ("fake_tg", "tg_api_id", "tg_api_hash", "proxy_enabled"):
        runtime_settings()._cache.pop(k, None)


async def _prime_runtime_cache(db) -> None:
    """Load the full EDITABLE keyset into the cache (like panel startup), so
    later mutations survive the TTL-based get_all rebuild."""
    await runtime_settings().get_all(db, force=True)


def _blank_env_tg_creds():
    """Temporarily zero the .env-provided creds on the cached Settings object
    (the project .env ships dummy TGDRIVE_TG_API_* values). Returns a restore fn."""
    from app.core.config import get_settings

    s = get_settings()
    old = (s.tg_api_id, s.tg_api_hash)
    s.tg_api_id, s.tg_api_hash = 0, ""
    return lambda: setattr(s, "tg_api_id", old[0]) or setattr(s, "tg_api_hash", old[1])


class FakeSession:
    def save(self):
        return "fake-session-string"


class FakeTelegramClient:
    """Records constructor args + awaited calls; stands in for the real client."""

    calls: list = []
    instances: list = []

    def __init__(self, session=None, api_id=0, api_hash="", proxy=None, **kw):
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = proxy
        self.connected = False
        self.sent_code_to = None
        self.disconnected = False
        self.session = session
        type(self).instances.append(self)

    async def connect(self):
        self.connected = True
        self.calls.append(("connect",))

    async def send_code_request(self, phone):
        self.sent_code_to = phone
        self.calls.append(("send_code", phone))

    async def sign_in(self, phone=None, code=None, password=None, **kw):
        if password is not None:
            self.calls.append(("sign_in_password", password))
        else:
            self.calls.append(("sign_in", phone, code))

    async def disconnect(self):
        self.disconnected = True
        self.calls.append(("disconnect",))

    def is_connected(self):
        return self.connected


@pytest.fixture()
def fake_telethon(monkeypatch):
    """app.api.accounts imports TelegramClient from telethon inside the endpoint
    functions, so patching the attribute on the telethon module covers it."""
    import telethon

    monkeypatch.setattr(telethon, "TelegramClient", FakeTelegramClient, raising=True)
    FakeTelegramClient.calls = []
    FakeTelegramClient.instances = []
    yield FakeTelegramClient
    FakeTelegramClient.calls = []
    FakeTelegramClient.instances = []


async def test_login_start_real_mode_uses_runtime_creds_and_awaits(client, token, fake_telethon):
    """fake_tg=0 + real creds saved from the panel → Telethon must be constructed
    with those creds and connect/send_code must actually run (awaited), so a real
    code request goes out. No fake account is created."""
    from app.core.models import AccountRepo
    from app.core.state import state

    await _prime_runtime_cache(state.db)
    _set_runtime(RUNTIME_REAL)
    try:
        before = len(await AccountRepo(state.db).list())
        r = await client.post(
            "/api/v1/accounts/login/start",
            json={"phone": "+989121111111", "label": "t"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "code_sent"
        assert body["login_id"], "login_id must be returned for the complete step"
        assert len(await AccountRepo(state.db).list()) == before, "no fake account in real mode"

        # the client was built with the runtime (panel) credentials
        assert fake_telethon.instances, "TelegramClient must be constructed"
        inst = fake_telethon.instances[-1]
        assert inst.api_id == 12345
        assert inst.api_hash == "0123456789abcdef0123456789abcdef"

        # the awaited calls really ran (regression: to_thread never executed them)
        assert ("connect",) in fake_telethon.calls
        assert ("send_code", "+989121111111") in fake_telethon.calls
    finally:
        _clear_runtime()


async def test_login_complete_sign_in_is_awaited(client, token, fake_telethon, monkeypatch):
    """The code step must await client.sign_in — with to_thread() it created the
    coroutine, returned 'ready', and the login never actually happened."""
    from app.api import accounts as acc
    from app.core.models import AccountRepo
    from app.core.state import state

    async def _no_refresh(account_id):
        pass

    monkeypatch.setattr(state.manager, "refresh_one_account", _no_refresh)
    await _prime_runtime_cache(state.db)
    _set_runtime(RUNTIME_REAL)
    inst = FakeTelegramClient(session=FakeSession())
    login_id = "login_test_complete"
    acc._logins[login_id] = {"client": inst, "phone": "+989121111111", "ts": time.time(), "label": "t"}
    try:
        r = await client.post(
            "/api/v1/accounts/login/complete",
            json={"login_id": login_id, "code": "12345"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ready"
        assert ("sign_in", "+989121111111", "12345") in fake_telethon.calls, "sign_in must actually run"
        assert inst.disconnected, "client must be disconnected after login"
        assert login_id not in acc._logins
        accounts = await AccountRepo(state.db).list()
        assert any(a["phone"] == "+989121111111" for a in accounts), "account persisted"
    finally:
        acc._logins.pop(login_id, None)
        _clear_runtime()


async def test_login_complete_2fa_password_path(client, token, fake_telethon):
    """code → password_needed → sign_in(password=…) → ready."""
    from app.api import accounts as acc

    class SessionPasswordNeededError(Exception):
        pass

    inst = FakeTelegramClient(session=FakeSession())

    async def sign_in_pw(phone=None, code=None, password=None, **kw):
        if password is None:
            raise SessionPasswordNeededError()
        fake_telethon.calls.append(("sign_in_password", password))

    inst.sign_in = sign_in_pw
    login_id = "login_test_2fa"
    acc._logins[login_id] = {"client": inst, "phone": "+989129999999", "ts": time.time(), "label": ""}
    try:
        r1 = await client.post(
            "/api/v1/accounts/login/complete",
            json={"login_id": login_id, "code": "12345"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200 and r1.json()["status"] == "password_needed"

        r2 = await client.post(
            "/api/v1/accounts/login/complete",
            json={"login_id": login_id, "code": "", "password": "s3cret"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "ready"
        assert ("sign_in_password", "s3cret") in fake_telethon.calls
    finally:
        acc._logins.pop(login_id, None)
        _clear_runtime()


async def test_login_start_real_mode_without_creds_fails_loud(client, token, fake_telethon):
    """Panel says real mode (fake_tg=0) but no api creds → explicit 400, not silence."""
    from app.core.state import state

    restore_creds = _blank_env_tg_creds()
    await _prime_runtime_cache(state.db)
    _set_runtime({"fake_tg": 0, "tg_api_id": 0, "tg_api_hash": ""})
    try:
        r = await client.post(
            "/api/v1/accounts/login/start",
            json={"phone": "+989121111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "API ID" in r.json()["detail"]
        assert not fake_telethon.instances, "must fail before constructing any client"
    finally:
        restore_creds()
        _clear_runtime()


async def test_login_start_proxy_used_when_enabled(client, token, fake_telethon):
    """With a proxy in the pool + proxy_enabled=1, login connects through it."""
    from app.core.models import ProxyRepo
    from app.core.state import state
    from app.services import proxy_service

    await _prime_runtime_cache(state.db)
    _set_runtime(RUNTIME_REAL)
    try:
        db = state.db
        pid = await ProxyRepo(db).create(
            host="10.0.0.9", port=1080, kind="socks5", label="t", username="u", password="p"
        )
        await ProxyRepo(db).set_enabled(pid, True)
        await ProxyRepo(db).set_check_result(pid, "ok", 12.0)
        # enable the pool globally (what the panel's toggle persists)
        _set_runtime({"proxy_enabled": 1})
        proxy_service.selector.invalidate()

        r = await client.post(
            "/api/v1/accounts/login/start",
            json={"phone": "+989123333333"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert fake_telethon.instances, "client must be constructed"
        assert fake_telethon.instances[-1].proxy is not None, "client must be built with the proxy"
        assert ("connect",) in fake_telethon.calls
    finally:
        proxy_service.selector.invalidate()
        _clear_runtime()


async def test_login_resend_capped_at_three(client, token, fake_telethon):
    """POST /login/resend re-sends via the SAME client and is capped at 3."""
    from app.core.state import state

    await _prime_runtime_cache(state.db)
    _set_runtime(RUNTIME_REAL)
    try:
        r = await client.post(
            "/api/v1/accounts/login/start",
            json={"phone": "+989124444444"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        lid = r.json()["login_id"]
        send_codes = fake_telethon.calls.count(("send_code", "+989124444444"))
        assert send_codes == 1
        for i in range(3):
            rr = await client.post(
                "/api/v1/accounts/login/resend",
                json={"login_id": lid},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rr.status_code == 200, rr.text
            assert rr.json()["resends_left"] == 2 - i
        # 4th resend must hit the server-side cap
        rr = await client.post(
            "/api/v1/accounts/login/resend",
            json={"login_id": lid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rr.status_code == 429
        assert fake_telethon.calls.count(("send_code", "+989124444444")) == 4
    finally:
        _clear_runtime()


async def test_login_complete_2fa_attempts_capped(client, token, fake_telethon, monkeypatch):
    """3 wrong 2FA passwords → 429 and the pending login is dropped."""
    from app.api import accounts as acc
    from app.core.state import state

    async def _no_refresh(account_id):
        pass

    monkeypatch.setattr(state.manager, "refresh_one_account", _no_refresh)
    await _prime_runtime_cache(state.db)
    _set_runtime(RUNTIME_REAL)

    class PasswordHashInvalidError(Exception):
        pass

    inst = FakeTelegramClient(session=FakeSession())

    async def sign_in_bad_pw(phone=None, code=None, password=None, **kw):
        if password is not None:
            raise PasswordHashInvalidError()
        fake_telethon.calls.append(("sign_in", phone, code))

    inst.sign_in = sign_in_bad_pw
    login_id = "login_test_pw_cap"
    acc._logins[login_id] = {"client": inst, "phone": "+989125555555", "ts": time.time(), "label": ""}
    try:
        # wrong password 1..2 → 400 with remaining-attempts detail
        for left in (2, 1):
            rr = await client.post(
                "/api/v1/accounts/login/complete",
                json={"login_id": login_id, "code": "", "password": "wrong"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rr.status_code == 400, rr.text
            assert f"{left} تلاش باقی مانده" in rr.json()["detail"]
        # 3rd wrong attempt → 429 and the login session is gone
        rr = await client.post(
            "/api/v1/accounts/login/complete",
            json={"login_id": login_id, "code": "", "password": "wrong"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rr.status_code == 429, rr.text
        assert login_id not in acc._logins
        assert inst.disconnected
    finally:
        acc._logins.pop(login_id, None)
        _clear_runtime()


async def test_login_start_env_fake_still_works(client, token):
    """No runtime overrides → env fake_tg=1 still auto-creates the fake account."""
    from app.core.models import AccountRepo
    from app.core.state import state

    before = len(await AccountRepo(state.db).list())
    r = await client.post(
        "/api/v1/accounts/login/start",
        json={"phone": "+989122222222"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert len(await AccountRepo(state.db).list()) == before + 1
