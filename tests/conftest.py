"""Shared fixtures: app with FakeTelegram backend + tmp SQLite database."""
from __future__ import annotations

import asyncio
import glob
import os
import shutil
import stat
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TGDRIVE_FAKE_TG", "1")
os.environ.setdefault("TGDRIVE_SECRET", "test-secret-please-change-me-32chars")
os.environ.setdefault("TGDRIVE_ADMIN_USERNAME", "admin")
os.environ.setdefault("TGDRIVE_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("TGDRIVE_DOWNLOAD_WORKERS", "2")
os.environ.setdefault("TGDRIVE_UPLOAD_WORKERS", "1")

#: every per-test data dir this session created that is STILL on disk. A dir only
#: leaves this list once it is really gone, so the session fixture's assert cannot
#: be fooled by a purge that silently failed (which is how ~146 dirs/run filled C:).
_USED_TMPDIRS: list[str] = []

#: (nodeid, tmpdir) for dirs whose purge failed, reported at session end so the
#: offending test can be named instead of leaving a mystery folder in TEMP.
_PURGE_FAILURES: list[tuple[str, str]] = []

#: every Settings instance this suite handed out. `get_settings()` is an lru_cache
#: that the `client` fixture mutates in place, so app code holding a reference can
#: still open a database against the *previous* test's dir; teardown re-points them
#: all at the session scratch dir (see `_park_settings`).
_LIVE_SETTINGS: list = []

#: one parking lot per session: a late `state.db_instance()` writes here instead of
#: resurrecting a per-test dir that was just purged, and the session sweep removes
#: it at the end.
_SESSION_SCRATCH: list[str] = []

TMP_PREFIX = "tgdrive-test-"
#: dirs older than this are leftovers from a crashed/killed run and are swept at
#: session start. Anything fresher may belong to a suite running right now.
STALE_TMP_SECONDS = 6 * 3600


def _force_rm(func, path, _exc) -> None:
    """`shutil.rmtree` error handler: clear read-only bits and retry the call.
    Windows marks freshly closed sqlite files read-only in some cases and a plain
    rmtree then dies with EACCES, leaking the whole dir."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass  # genuinely locked (sqlite still open) -> the retry loop handles it


def purge_dir(path: str, attempts: int = 6) -> bool:
    """Remove a directory tree, tolerating Windows' habit of holding freshly
    closed sqlite/WAL handles for a moment. Returns True when it is gone."""
    for i in range(attempts):
        if not os.path.exists(path):
            return True
        try:
            shutil.rmtree(path, onerror=_force_rm)
            return True
        except OSError:
            # sqlite (WAL) handles are released asynchronously on Windows; give
            # the aiosqlite worker thread a moment before trying again.
            time.sleep(0.1 * (i + 1))
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)  # last resort: best effort
    return not os.path.exists(path)


def _session_scratch() -> str:
    """A per-session parking lot for late `db_instance()` calls. Created lazily,
    purged by the session fixture, and registered in the ledger so the leak assert
    covers it too."""
    if not _SESSION_SCRATCH:
        _SESSION_SCRATCH.append(tempfile.mkdtemp(prefix=TMP_PREFIX + "session-"))
    return _SESSION_SCRATCH[0]


def _park_settings(old_dir: str) -> None:
    """Re-point every Settings instance this suite handed out at the session
    scratch dir, so nothing can resurrect `old_dir` after it has been deleted.

    `get_settings()` is an lru_cache and the `client` fixture mutates the cached
    object in place, so app code can still hold a reference to it: a late
    `state.db_instance()` (stray task, lazy dependency) then runs
    `Database.connect()`, whose `os.makedirs(dirname)` puts the just-purged dir
    straight back on disk with a fresh sqlite file inside — the last 2 leaked dirs
    per run had exactly that shape (only `test.sqlite3`, no `tmp/`).
    """
    scratch = _session_scratch()
    seen = set()
    for s in list(_LIVE_SETTINGS) + [_live_cached_settings()]:
        if s is None or id(s) in seen:
            continue
        seen.add(id(s))
        s.data_dir = scratch
        s.db_path = os.path.join(scratch, "parked.sqlite3")
        s.upload_tmp_dir = os.path.join(scratch, "tmp")
    _LIVE_SETTINGS.clear()


def _live_cached_settings():
    """The object currently held by the `get_settings` lru_cache (or None)."""
    from app.core.config import get_settings

    return get_settings()


def sweep_stale_tmpdirs(base: str | None = None, older_than: float = STALE_TMP_SECONDS) -> list[str]:
    """Delete leaked `tgdrive-test-*` dirs from earlier runs. Skips anything
    younger than ``older_than`` so a concurrent suite is never disturbed."""
    base = base or tempfile.gettempdir()
    removed = []
    now = time.time()
    for path in glob.glob(os.path.join(base, TMP_PREFIX + "*")):
        try:
            if now - os.path.getmtime(path) < older_than:
                continue  # probably a live run
        except OSError:
            continue
        if purge_dir(path):
            removed.append(path)
    return removed


@pytest.fixture(scope="session")
def tmp_hygiene():
    """Expose the temp-dir helpers to tests. (conftest itself is not importable
    from a test module, so the helpers travel as a fixture.)"""
    return {
        "prefix": TMP_PREFIX,
        "sweep": sweep_stale_tmpdirs,
        "purge": purge_dir,
        "used": _USED_TMPDIRS,
        "failures": _PURGE_FAILURES,
        "register": lambda s: _LIVE_SETTINGS.append(s),
        "park": _park_settings,
    }


@pytest.fixture(scope="session", autouse=True)
def _tmp_hygiene():
    """Start from a clean slate, then guarantee this run leaves TEMP as it found it.

    Anything this session created is removed here even when a per-test purge
    failed, and the run FAILS if a dir survives - a silent leak is what filled the
    user's C: drive to 100 % once.
    """
    _USED_TMPDIRS.clear()
    _PURGE_FAILURES.clear()
    sweep_stale_tmpdirs()
    yield
    for d in [d for d in _USED_TMPDIRS if os.path.exists(d)] + _SESSION_SCRATCH:
        purge_dir(d)  # last chance: handles may have been released by now
    leaked = [d for d in _USED_TMPDIRS if os.path.exists(d)]
    still = [p for p in glob.glob(os.path.join(tempfile.gettempdir(), TMP_PREFIX + "*"))
             if os.path.exists(p) and p not in _USED_TMPDIRS]
    assert not leaked and not still, (
        f"{len(leaked) + len(still)} test temp dir(s) leaked into TEMP (this filled the disk once). "
        f"purge failures: {_PURGE_FAILURES[:5]}; leaked: {leaked[:5]}; untracked: {still[:5]}"
    )


@pytest.fixture()
async def client(request):
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
    tmpdir = tempfile.mkdtemp(prefix=TMP_PREFIX)
    _USED_TMPDIRS.append(tmpdir)
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
    # both instances stay reachable for the whole test; teardown re-points them
    _LIVE_SETTINGS.extend([s, fresh])

    try:
        async with LifespanManager(app):
            # ensure one healthy fake backend exists for every test
            from app.core.models import AccountRepo
            from app.core.security import encrypt_str

            acc_id = await AccountRepo(state.db).create("test-acc", "+989120000000", "")
            await AccountRepo(state.db).set_session(acc_id, encrypt_str("fake-session::test"), "ready")
            await state.manager.refresh_one_account(acc_id)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                yield ac
    finally:
        # 1) point every still-cached Settings object away from the doomed dir and
        #    close the DB BEFORE deleting it. Otherwise a late `state.db_instance()`
        #    (stray task / lazy dependency) re-creates the directory via
        #    `Database.connect()`'s os.makedirs and leaves a fresh test.sqlite3 in
        #    a folder nobody owns any more - that was the last 2 dirs per run.
        _park_settings(tmpdir)
        if state.db is not None:
            try:
                await state.db.close()
            except Exception:
                pass
        state.db = None
        state.manager = None
        state.queue = None
        state.bots = None
        state.janitor = None

        # 2) ALWAYS drop the per-test data dir, even when the test failed or the app
        # raised on startup — one leaked dir per test is what filled the disk.
        #    The ledger entry only goes away once the dir is really gone, so a failed
        #    purge fails the session instead of vanishing from the accounting.
        if not purge_dir(tmpdir):
            _PURGE_FAILURES.append((request.node.nodeid, tmpdir))
        elif tmpdir in _USED_TMPDIRS:
            _USED_TMPDIRS.remove(tmpdir)

        from app.tg.fake import FakeBackend

        FakeBackend.STORE.clear()
        from app.core.rate_limit import limiter, quota

        limiter._buckets.clear()
        quota._bytes.clear()
        quota._day.clear()


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


@pytest.fixture()
async def db():
    # state.db is wired by the client fixture; expose it after setup
    from app.core.state import state

    yield state.db

    state.db = None
    state.manager = None
    state.queue = None
    state.bots = None
    state.janitor = None


AUTH = {"Authorization": "Bearer x"}  # replaced per-test with real key
