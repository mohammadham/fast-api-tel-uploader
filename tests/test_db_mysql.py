"""MySQL backend tests: dialect/translation only, no server required.

A live MySQL round-trip needs a real server (see docs/test-notes in AGENTS.md),
so these cover everything that can break without one: URL normalisation,
``?`` -> named-param translation, the DDL, the reserved ``key`` column and the
per-backend ``insert()`` contract.
"""
from __future__ import annotations

import pytest

from app.core.db import (
    SCHEMA_MYSQL,
    SCHEMA_MYSQL_INDEXES,
    Database,
    MysqlDatabase,
    PgDatabase,
    _schema_statements,
)
from app.core.settings_service import _key_col


def test_normalize_mysql_url():
    url, args = MysqlDatabase.normalize_url("mysql://u:p@db:3306/tgdrive")
    assert url == "mysql+aiomysql://u:p@db:3306/tgdrive"
    assert args["charset"] == "utf8mb4"


def test_normalize_mariadb_url():
    url, _ = MysqlDatabase.normalize_url("mariadb://u:p@db/tgdrive")
    assert url.startswith("mysql+aiomysql://")


def test_normalize_leaves_aiomysql_url_untouched():
    raw = "mysql+aiomysql://u:p@db/tgdrive"
    assert MysqlDatabase.normalize_url(raw)[0] == raw


def test_placeholders_translate_to_named_params():
    q, p = MysqlDatabase._mysql_sql(
        "SELECT * FROM files WHERE id=? AND status=?", ("abc", "ready")
    )
    assert str(q) == "SELECT * FROM files WHERE id=:p0 AND status=:p1"
    assert p == {"p0": "abc", "p1": "ready"}


def test_placeholder_count_mismatch_raises():
    with pytest.raises(ValueError):
        MysqlDatabase._mysql_sql("SELECT * FROM files WHERE id=?", ())


def test_mysql_schema_dialect():
    assert "AUTO_INCREMENT" in SCHEMA_MYSQL
    assert "SERIAL" not in SCHEMA_MYSQL
    assert "DOUBLE PRECISION" not in SCHEMA_MYSQL
    # MySQL rejects literal defaults on TEXT/BLOB columns
    assert "TEXT NOT NULL DEFAULT" not in SCHEMA_MYSQL


def test_mysql_schema_statements_split_cleanly():
    stmts = _schema_statements(SCHEMA_MYSQL)
    assert len(stmts) == SCHEMA_MYSQL.count("CREATE TABLE")
    assert all(s.startswith("CREATE TABLE") for s in stmts)
    assert SCHEMA_MYSQL_INDEXES


def test_key_column_quoted_only_for_mysql():
    assert _key_col(MysqlDatabase("mysql://u:p@h/db")) == "`key`"
    assert _key_col(Database(":memory:")) == "key"
    assert _key_col(PgDatabase("postgres://u:p@h/db")) == "key"


def test_backends_expose_dialect_and_insert():
    assert Database.dialect == "sqlite"
    assert PgDatabase.dialect == "postgres"
    assert MysqlDatabase.dialect == "mysql"
    for cls in (Database, PgDatabase, MysqlDatabase):
        assert callable(getattr(cls, "insert", None))


async def test_setup_status_reports_engine(client, token):
    resp = await client.get(
        "/api/v1/admin/setup/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    database = resp.json()["database"]
    assert database["engine"] == "sqlite"
    assert database["mysql_configured"] is False


async def test_db_test_rejects_unsupported_url(client, token):
    resp = await client.post(
        "/api/v1/admin/db/test",
        json={"url": "sqlite:///nope.db"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "mysql" in body["error"].lower()


async def test_db_test_requires_admin(client):
    resp = await client.post("/api/v1/admin/db/test", json={"url": "mysql://u:p@h/db"})
    assert resp.status_code == 401
