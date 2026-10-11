"""The test suite must not leak `tgdrive-test-*` dirs into TEMP.

It used to create one dir per test with no cleanup at all: 22,335 dirs (~3.4 GB)
later the user's C: drive was 100 % full and the suite failed with
`sqlite3.OperationalError: database or disk is full` plus bogus multipart
body-parse errors. conftest now purges each dir in a `finally` and sweeps stale
leftovers at session start; the session fixture also fails the run if anything
survived.

The helpers come through the `tmp_hygiene` fixture because conftest is not
importable as a module.
"""
from __future__ import annotations

import os
import stat
import time

import pytest

_seen: list[str] = []


@pytest.mark.asyncio
async def test_tmpdir_exists_while_the_test_runs(client, tmp_hygiene):
    """The app needs its data dir alive during the test — the dir must exist here."""
    from app.core.config import get_settings

    data_dir = get_settings().data_dir
    assert os.path.isdir(data_dir), data_dir
    assert os.path.basename(data_dir).startswith(tmp_hygiene["prefix"]), data_dir
    assert os.path.isfile(get_settings().db_path), "the fixture's sqlite file lives in it"
    _seen.append(data_dir)


@pytest.mark.asyncio
async def test_previous_test_tmpdir_was_removed(client):
    """...and the previous test's dir must already be gone (definition order)."""
    assert _seen, "the first test in this module must run before this one"
    assert not os.path.exists(_seen[0]), f"leaked: {_seen[0]}"


def test_sweep_removes_stale_dirs_and_keeps_fresh_ones(tmp_path, tmp_hygiene):
    base = str(tmp_path)
    prefix = tmp_hygiene["prefix"]
    stale = tmp_path / (prefix + "stale")
    fresh = tmp_path / (prefix + "fresh")
    for d in (stale, fresh):
        (d / "tmp").mkdir(parents=True)
        (d / "test.sqlite3").write_bytes(b"x")
    # backdate one so it looks like a leftover from a crashed run
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    removed = tmp_hygiene["sweep"](base=base, older_than=6 * 3600)

    assert str(stale) in removed
    assert not stale.exists(), "stale leftover must be deleted"
    assert fresh.exists(), "a dir that may belong to a live run must be left alone"
    assert str(fresh) not in removed


def test_sweep_is_safe_on_a_missing_base_dir(tmp_path, tmp_hygiene):
    assert tmp_hygiene["sweep"](base=str(tmp_path / "does-not-exist")) == []


def test_purge_dir_reports_success_and_is_idempotent(tmp_path, tmp_hygiene):
    d = tmp_path / (tmp_hygiene["prefix"] + "gone")
    (d / "nested").mkdir(parents=True)
    (d / "nested" / "f.bin").write_bytes(b"123")
    assert tmp_hygiene["purge"](str(d)) is True
    assert not d.exists()
    assert tmp_hygiene["purge"](str(d)) is True  # already gone is still success


def test_purge_handles_read_only_files(tmp_path, tmp_hygiene):
    """A read-only sqlite file must not make the whole dir survive (Windows)."""
    d = tmp_path / (tmp_hygiene["prefix"] + "ro")
    d.mkdir()
    f = d / "test.sqlite3"
    f.write_bytes(b"x")
    os.chmod(f, stat.S_IREAD)
    assert tmp_hygiene["purge"](str(d)) is True, "read-only files must still be removed"
    assert not d.exists()


@pytest.mark.asyncio
async def test_late_db_connect_cannot_resurrect_a_purged_dir(tmp_path, tmp_hygiene):
    """Regression: a late `state.db_instance()` used to re-create the purged dir.

    `Database.connect()` runs `os.makedirs(dirname)`, so any code path that opens a
    database after the fixture deleted its temp dir puts the folder straight back
    on disk with a fresh sqlite file — that was the last 2 leaked dirs per run
    (each containing only `test.sqlite3`, no `tmp/`). Teardown now parks every live
    Settings object at the session scratch dir, so the connect lands there.
    """
    from app.core import state as app_state
    from app.core.config import get_settings

    doomed = tmp_path / (tmp_hygiene["prefix"] + "doomed")
    doomed.mkdir()
    doomed_db = doomed / "test.sqlite3"

    s = get_settings()
    before = (s.data_dir, s.db_path, s.upload_tmp_dir)
    s.data_dir = str(doomed)
    s.db_path = str(doomed_db)
    s.upload_tmp_dir = str(doomed / "tmp")
    tmp_hygiene["register"](s)

    app_state.state.db = None
    db = await app_state.state.db_instance()  # the "late" caller
    try:
        assert os.path.isfile(str(doomed_db)), "sanity: connect created the file"
    finally:
        await db.close()
        app_state.state.db = None
        assert tmp_hygiene["purge"](str(doomed)) is True

        # simulate conftest's teardown parking, then let another caller connect
        tmp_hygiene["park"](str(doomed))
        db2 = await app_state.state.db_instance()
        try:
            parked_path = str(db2.path)
        finally:
            await db2.close()
            app_state.state.db = None
        assert not doomed.exists(), f"the purged dir was resurrected at {doomed}"
        assert not parked_path.startswith(str(doomed)), parked_path
        s.data_dir, s.db_path, s.upload_tmp_dir = before
