"""Async SQLite access layer (WAL) with schema bootstrap.

A single shared aiosqlite connection guarded by an asyncio lock keeps write
serialization simple and avoids "database is locked" churn. WAL allows
concurrent readers during a write, which matches our workload (API reads while
queue workers write progress).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  key_hash TEXT UNIQUE NOT NULL,
  key_prefix TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT 'read,write',
  rpm INTEGER NOT NULL DEFAULT 120,
  daily_quota_bytes INTEGER NOT NULL DEFAULT 0,
  used_bytes_today REAL NOT NULL DEFAULT 0,
  quota_day TEXT NOT NULL DEFAULT '',
  expires_at REAL,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_used_at REAL,
  storage_chat TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tg_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '',
  session_enc TEXT NOT NULL DEFAULT '',
  is_premium INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  storage_chat_id TEXT NOT NULL DEFAULT 'me',
  status TEXT NOT NULL DEFAULT 'pending',
  flood_until REAL NOT NULL DEFAULT 0,
  circuit_open_until REAL NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  uploads_done INTEGER NOT NULL DEFAULT 0,
  downloads_done INTEGER NOT NULL DEFAULT 0,
  bytes_up INTEGER NOT NULL DEFAULT 0,
  bytes_down INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  token_enc TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL DEFAULT 'application/octet-stream',
  sha256 TEXT NOT NULL DEFAULT '',
  uploader TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'api',
  status TEXT NOT NULL DEFAULT 'queued',
  parts INTEGER NOT NULL DEFAULT 1,
  storage_chat TEXT NOT NULL DEFAULT '',
  message_ids TEXT NOT NULL DEFAULT '[]',
  downloads INTEGER NOT NULL DEFAULT 0,
  bytes_served INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  ready_at REAL,
  error TEXT NOT NULL DEFAULT '',
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  scope TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_files_storage_chat_created ON files(storage_chat, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_folder_created ON files(folder_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);
CREATE TABLE IF NOT EXISTS file_parts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  priority INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  correlation_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  payload TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 5,
  next_run_at REAL NOT NULL DEFAULT 0,
  lease_until REAL NOT NULL DEFAULT 0,
  lease_owner TEXT NOT NULL DEFAULT '',
  origin_node TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  finished_at REAL,
  error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC, seq);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS upload_sessions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL DEFAULT 'application/octet-stream',
  offset INTEGER NOT NULL DEFAULT 0,
  folder_path TEXT NOT NULL DEFAULT '',
  storage_chat TEXT NOT NULL DEFAULT '',
  uploader TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS eitaa_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  token_enc TEXT NOT NULL,
  chat_id TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_tokens (
  jti TEXT PRIMARY KEY,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  pwd_hash TEXT NOT NULL DEFAULT '',
  max_downloads INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL DEFAULT 0,
  updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS system_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  hostname TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL DEFAULT 0,
  last_heartbeat REAL NOT NULL DEFAULT 0,
  workers_dl INTEGER NOT NULL DEFAULT 0,
  workers_ul INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proxies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'socks5',
  host TEXT NOT NULL,
  port INTEGER NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  password_enc TEXT NOT NULL DEFAULT '',
  secret_hex TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',
  latency_ms REAL NOT NULL DEFAULT -1,
  last_checked_at REAL NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'storage',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_error TEXT NOT NULL DEFAULT '',
  last_backup_message_id INTEGER,
  last_backup_at REAL,
  last_backup_bytes INTEGER NOT NULL DEFAULT 0,
  last_backup_files INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_file_parts_file ON file_parts(file_id, idx);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""

# PostgreSQL equivalents (used when TGDRIVE_DATABASE_URL points at postgres)
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  key_hash TEXT UNIQUE NOT NULL,
  key_prefix TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT 'read,write',
  rpm INTEGER NOT NULL DEFAULT 120,
  daily_quota_bytes INTEGER NOT NULL DEFAULT 0,
  used_bytes_today DOUBLE PRECISION NOT NULL DEFAULT 0,
  quota_day TEXT NOT NULL DEFAULT '',
  expires_at DOUBLE PRECISION,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL,
  last_used_at DOUBLE PRECISION,
  backend TEXT NOT NULL DEFAULT '',
  storage_chat TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tg_accounts (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '',
  session_enc TEXT NOT NULL DEFAULT '',
  is_premium INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  storage_chat_id TEXT NOT NULL DEFAULT 'me',
  status TEXT NOT NULL DEFAULT 'pending',
  flood_until DOUBLE PRECISION NOT NULL DEFAULT 0,
  circuit_open_until DOUBLE PRECISION NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  uploads_done INTEGER NOT NULL DEFAULT 0,
  downloads_done INTEGER NOT NULL DEFAULT 0,
  bytes_up INTEGER NOT NULL DEFAULT 0,
  bytes_down INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_tokens (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  token_enc TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL DEFAULT 'application/octet-stream',
  sha256 TEXT NOT NULL DEFAULT '',
  uploader TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'api',
  status TEXT NOT NULL DEFAULT 'queued',
  parts INTEGER NOT NULL DEFAULT 1,
  storage_chat TEXT NOT NULL DEFAULT '',
  message_ids TEXT NOT NULL DEFAULT '[]',
  downloads INTEGER NOT NULL DEFAULT 0,
  bytes_served INTEGER NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL,
  ready_at DOUBLE PRECISION,
  error TEXT NOT NULL DEFAULT '',
  deleted_at DOUBLE PRECISION,
  thumb_message_id INTEGER,
  thumb_mime TEXT NOT NULL DEFAULT '',
  backend TEXT NOT NULL DEFAULT '',
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS folders (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
  created_at DOUBLE PRECISION NOT NULL,
  scope TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS file_parts (
  id SERIAL PRIMARY KEY,
  file_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  priority INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  correlation_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  payload TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 5,
  next_run_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
  lease_owner TEXT NOT NULL DEFAULT '',
  origin_node TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  finished_at DOUBLE PRECISION,
  error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC, seq);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE TABLE IF NOT EXISTS audit_log (
  id SERIAL PRIMARY KEY,
  ts DOUBLE PRECISION NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS upload_sessions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL DEFAULT 'application/octet-stream',
  offset INTEGER NOT NULL DEFAULT 0,
  uploader TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS eitaa_accounts (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL,
  token_enc TEXT NOT NULL,
  chat_id TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_tokens (
  jti TEXT PRIMARY KEY,
  expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  id SERIAL PRIMARY KEY,
  file_id TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  pwd_hash TEXT NOT NULL DEFAULT '',
  max_downloads INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS system_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  hostname TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  started_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_heartbeat DOUBLE PRECISION NOT NULL DEFAULT 0,
  workers_dl INTEGER NOT NULL DEFAULT 0,
  workers_ul INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proxies (
  id SERIAL PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'socks5',
  host TEXT NOT NULL,
  port INTEGER NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  password_enc TEXT NOT NULL DEFAULT '',
  secret_hex TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',
  latency_ms DOUBLE PRECISION NOT NULL DEFAULT -1,
  last_checked_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
  id SERIAL PRIMARY KEY,
  chat TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'storage',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_error TEXT NOT NULL DEFAULT '',
  last_backup_message_id BIGINT,
  last_backup_at DOUBLE PRECISION,
  last_backup_bytes BIGINT NOT NULL DEFAULT 0,
  last_backup_files INTEGER NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_file_parts_file ON file_parts(file_id, idx);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
 CREATE INDEX IF NOT EXISTS idx_files_storage_chat_created ON files(storage_chat, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_folder_created ON files(folder_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_nextrun ON jobs(status, next_run_at);
"""


# MySQL / MariaDB equivalents (used when TGDRIVE_DATABASE_URL points at mysql)
# Notes: no RETURNING (auto ids use LAST_INSERT_ID), TEXT/BLOB columns cannot
# carry a literal DEFAULT on MySQL, and identifiers are stored lowercase.
# `key` (settings/system_meta) is reserved in MySQL — queries quote it.
SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'admin',
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  key_hash VARCHAR(128) UNIQUE NOT NULL,
  key_prefix VARCHAR(64) NOT NULL,
  scopes VARCHAR(255) NOT NULL DEFAULT 'read,write',
  rpm INT NOT NULL DEFAULT 120,
  daily_quota_bytes BIGINT NOT NULL DEFAULT 0,
  used_bytes_today DOUBLE NOT NULL DEFAULT 0,
  quota_day VARCHAR(16) NOT NULL DEFAULT '',
  expires_at DOUBLE NULL,
  revoked INT NOT NULL DEFAULT 0,
  created_at DOUBLE NOT NULL,
  last_used_at DOUBLE NULL,
  backend VARCHAR(32) NOT NULL DEFAULT '',
  storage_chat VARCHAR(255) NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tg_accounts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  label VARCHAR(255) NOT NULL,
  phone VARCHAR(64) NOT NULL DEFAULT '',
  session_enc MEDIUMTEXT NOT NULL,
  is_premium INT NOT NULL DEFAULT 0,
  enabled INT NOT NULL DEFAULT 1,
  storage_chat_id VARCHAR(255) NOT NULL DEFAULT 'me',
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  flood_until DOUBLE NOT NULL DEFAULT 0,
  circuit_open_until DOUBLE NOT NULL DEFAULT 0,
  error_count INT NOT NULL DEFAULT 0,
  uploads_done INT NOT NULL DEFAULT 0,
  downloads_done INT NOT NULL DEFAULT 0,
  bytes_up BIGINT NOT NULL DEFAULT 0,
  bytes_down BIGINT NOT NULL DEFAULT 0,
  last_error VARCHAR(2000) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  label VARCHAR(255) NOT NULL,
  token_enc MEDIUMTEXT NOT NULL,
  enabled INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  last_error VARCHAR(2000) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(512) NOT NULL,
  parent_id INT NULL,
  created_at DOUBLE NOT NULL,
  scope VARCHAR(255) NOT NULL DEFAULT '',
  CONSTRAINT fk_folders_parent FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS files (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(512) NOT NULL,
  size BIGINT NOT NULL,
  mime VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
  sha256 VARCHAR(128) NOT NULL DEFAULT '',
  uploader VARCHAR(255) NOT NULL DEFAULT '',
  source VARCHAR(32) NOT NULL DEFAULT 'api',
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  parts INT NOT NULL DEFAULT 1,
  storage_chat VARCHAR(255) NOT NULL DEFAULT '',
  message_ids VARCHAR(2000) NOT NULL DEFAULT '[]',
  downloads INT NOT NULL DEFAULT 0,
  bytes_served BIGINT NOT NULL DEFAULT 0,
  created_at DOUBLE NOT NULL,
  ready_at DOUBLE NULL,
  error VARCHAR(2000) NOT NULL DEFAULT '',
  deleted_at DOUBLE NULL,
  thumb_message_id INT NULL,
  thumb_mime VARCHAR(128) NOT NULL DEFAULT '',
  backend VARCHAR(32) NOT NULL DEFAULT '',
  folder_id INT NULL,
  blocked INT NOT NULL DEFAULT 0,
  CONSTRAINT fk_files_folder FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS file_parts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  file_id VARCHAR(64) NOT NULL,
  idx INT NOT NULL,
  message_id BIGINT NOT NULL,
  size BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id VARCHAR(64) PRIMARY KEY,
  kind VARCHAR(64) NOT NULL,
  priority INT NOT NULL,
  seq BIGINT NOT NULL,
  correlation_id VARCHAR(128) NOT NULL DEFAULT '',
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  payload LONGTEXT NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  max_retries INT NOT NULL DEFAULT 5,
  next_run_at DOUBLE NOT NULL DEFAULT 0,
  lease_until DOUBLE NOT NULL DEFAULT 0,
  lease_owner VARCHAR(255) NOT NULL DEFAULT '',
  origin_node VARCHAR(128) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL,
  updated_at DOUBLE NOT NULL,
  finished_at DOUBLE NULL,
  error VARCHAR(2000) NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ts DOUBLE NOT NULL,
  actor VARCHAR(255) NOT NULL,
  action VARCHAR(128) NOT NULL,
  target VARCHAR(255) NOT NULL DEFAULT '',
  ip VARCHAR(64) NOT NULL DEFAULT '',
  details LONGTEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_sessions (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(512) NOT NULL,
  size BIGINT NOT NULL,
  mime VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
  offset BIGINT NOT NULL DEFAULT 0,
  folder_path VARCHAR(1024) NOT NULL DEFAULT '',
  storage_chat VARCHAR(255) NOT NULL DEFAULT '',
  uploader VARCHAR(255) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS eitaa_accounts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  label VARCHAR(255) NOT NULL,
  token_enc MEDIUMTEXT NOT NULL,
  chat_id VARCHAR(255) NOT NULL DEFAULT '',
  enabled INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  last_error VARCHAR(2000) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_tokens (
  jti VARCHAR(64) PRIMARY KEY,
  expires_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  id INT AUTO_INCREMENT PRIMARY KEY,
  file_id VARCHAR(64) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL,
  pwd_hash VARCHAR(255) NOT NULL DEFAULT '',
  max_downloads INT NOT NULL DEFAULT 0,
  hits INT NOT NULL DEFAULT 0,
  disabled INT NOT NULL DEFAULT 0,
  created_at DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  `key` VARCHAR(128) PRIMARY KEY,
  value VARCHAR(4096) NOT NULL DEFAULT '',
  updated_at DOUBLE NOT NULL DEFAULT 0,
  updated_by VARCHAR(255) NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS system_meta (
  `key` VARCHAR(128) PRIMARY KEY,
  value VARCHAR(4096) NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nodes (
  node_id VARCHAR(128) PRIMARY KEY,
  hostname VARCHAR(255) NOT NULL DEFAULT '',
  version VARCHAR(64) NOT NULL DEFAULT '',
  started_at DOUBLE NOT NULL DEFAULT 0,
  last_heartbeat DOUBLE NOT NULL DEFAULT 0,
  workers_dl INT NOT NULL DEFAULT 0,
  workers_ul INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proxies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  label VARCHAR(255) NOT NULL DEFAULT '',
  kind VARCHAR(32) NOT NULL DEFAULT 'socks5',
  host VARCHAR(255) NOT NULL,
  port INT NOT NULL,
  username VARCHAR(255) NOT NULL DEFAULT '',
  password_enc VARCHAR(4096) NOT NULL DEFAULT '',
  secret_hex VARCHAR(255) NOT NULL DEFAULT '',
  enabled INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  latency_ms DOUBLE NOT NULL DEFAULT -1,
  last_checked_at DOUBLE NOT NULL DEFAULT 0,
  last_error VARCHAR(2000) NOT NULL DEFAULT '',
  created_at DOUBLE NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
  id INT AUTO_INCREMENT PRIMARY KEY,
  chat VARCHAR(255) NOT NULL UNIQUE,
  label VARCHAR(255) NOT NULL DEFAULT '',
  kind VARCHAR(32) NOT NULL DEFAULT 'storage',
  enabled INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  last_error VARCHAR(2000) NOT NULL DEFAULT '',
  last_backup_message_id BIGINT NULL,
  last_backup_at DOUBLE NULL,
  last_backup_bytes BIGINT NOT NULL DEFAULT 0,
  last_backup_files INT NOT NULL DEFAULT 0,
  created_at DOUBLE NOT NULL DEFAULT 0
);
"""

# CREATE INDEX IF NOT EXISTS is not portable to MySQL: indexes are created
# individually and a duplicate-key error just means "already present".
SCHEMA_MYSQL_INDEXES = (
    "CREATE INDEX idx_folders_parent ON folders(parent_id)",
    "CREATE INDEX idx_files_storage_chat_created ON files(storage_chat, created_at)",
    "CREATE INDEX idx_files_folder_created ON files(folder_id, created_at)",
    "CREATE INDEX idx_files_created ON files(created_at)",
    "CREATE INDEX idx_file_parts_file ON file_parts(file_id, idx)",
    "CREATE INDEX idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX idx_jobs_status ON jobs(status, priority, seq)",
    "CREATE INDEX idx_jobs_status_nextrun ON jobs(status, next_run_at)",
    "CREATE INDEX idx_files_status ON files(status)",
)


def _schema_statements(schema: str) -> List[str]:
    """Split a DDL batch into single statements.

    MySQL's wire protocol rejects multi-statement batches unless the client
    opts in, and none of our DDL carries a ``;`` inside a literal.
    """
    return [s.strip() for s in schema.split(";") if s.strip()]


def _now() -> float:
    return time.time()


class Database:
    is_sqlite = True
    dialect = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        # light migrations for pre-existing databases (CREATE TABLE IF NOT EXISTS
        # cannot add columns to tables that already exist)
        for stmt in (
            "ALTER TABLE jobs ADD COLUMN correlation_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN origin_node TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN deleted_at REAL",
            "ALTER TABLE files ADD COLUMN thumb_message_id INTEGER",
            "ALTER TABLE files ADD COLUMN thumb_mime TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN backend TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE api_keys ADD COLUMN backend TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE api_keys ADD COLUMN storage_chat TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL",
            "ALTER TABLE upload_sessions ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE upload_sessions ADD COLUMN storage_chat TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE upload_sessions ADD COLUMN uploader TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE channels ADD COLUMN last_backup_message_id INTEGER",
            "ALTER TABLE channels ADD COLUMN last_backup_at REAL",
            "ALTER TABLE channels ADD COLUMN last_backup_bytes INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE channels ADD COLUMN last_backup_files INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE folders ADD COLUMN scope TEXT NOT NULL DEFAULT ''",
        ):
            try:
                await self._conn.execute(stmt)
            except Exception:
                pass  # column already present
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "database not connected"
        return self._conn

    @asynccontextmanager
    async def tx(self):
        """Serialized transaction context."""
        async with self._lock:
            try:
                yield self.conn
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    # ---------- generic helpers ----------
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self.tx() as conn:
            cur = await conn.execute(sql, tuple(params))
            return cur.rowcount

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        cur = await self.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        cur = await self.conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cur = await self.conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None

    async def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run an INSERT and return its generated id."""
        await self.execute(sql, params)
        return await self.last_insert_rowid()

    # ---------- SQLite-specific helpers (no-op / error on PG) ----------
    async def last_insert_rowid(self) -> int:
        return int(await self.scalar("SELECT last_insert_rowid()") or 0)

    # ---------- audit ----------
    async def audit(self, actor: str, action: str, target: str = "", ip: str = "", details: str = "") -> None:
        await self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, ip, details) VALUES(?,?,?,?,?,?)",
            (_now(), actor, action, target, ip, details),
        )

    async def audit_row(self, action: str) -> Optional[dict]:
        row = await self.fetch_one("SELECT * FROM audit_log WHERE action=? ORDER BY id DESC LIMIT 1", (action,))
        return dict(row) if row else None


class PgDatabase:
    """PostgreSQL backend with the same interface as Database.

    Activated when TGDRIVE_DATABASE_URL is a postgres:// / postgresql:// URL.
    SQL strings across the codebase use SQLite ``?`` placeholders; they are
    translated to SQLAlchemy named parameters transparently. Row results are
    plain dicts (matching aiosqlite Row→dict conversion upstream).
    """

    is_sqlite = False
    dialect = "postgres"

    def __init__(self, url: str) -> None:
        self.url = url
        self._engine = None

    @staticmethod
    def normalize_url(url: str) -> tuple:
        """Render/Heroku-style postgres URL → asyncpg engine URL + connect args.

        libpq's ``sslmode`` query param is not an asyncpg connect kwarg; it is
        translated to ``ssl=True`` (require). Internal DB URLs without the
        param stay untouched.
        """
        ssl_required = False
        if "sslmode=" in url:
            from urllib.parse import urlparse, parse_qs, urlencode

            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            mode = (qs.pop("sslmode", [""])[0] or "").lower()
            ssl_required = mode in ("require", "verify-ca", "verify-full", "prefer")
            rest = urlencode({k: v[0] for k, v in qs.items()})
            url = parsed._replace(query=rest).geturl()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        connect_args = {"ssl": True} if ssl_required else {}
        return url, connect_args

    async def connect(self) -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine_url, connect_args = self.normalize_url(self.url)
        self._engine = create_async_engine(
            engine_url, pool_pre_ping=True, pool_size=10, max_overflow=20,
            connect_args=connect_args,
        )
        async with self._engine.begin() as conn:
            await conn.execute(text(SCHEMA_PG))

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    # ---------- SQL translation ----------
    @staticmethod
    def _pg_sql(sql: str, params: Iterable[Any]) -> tuple[str, dict]:
        from sqlalchemy import text

        parts: list[str] = []
        names: list[str] = []
        idx = 0
        sql_out: list[str] = []
        i = 0
        while i < len(sql):
            ch = sql[i]
            if ch == "?":
                name = f"p{idx}"
                names.append(name)
                sql_out.append(f":{name}")
                idx += 1
            else:
                sql_out.append(ch)
            i += 1
        translated = "".join(sql_out)
        plist = list(params)
        if len(plist) != idx:
            raise ValueError(f"placeholder mismatch: {idx} params expected, {len(plist)} given: {translated[:120]}")
        return text(translated), {f"p{k}": v for k, v in enumerate(plist)}

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        q, p = self._pg_sql(sql, params)
        async with self._engine.begin() as conn:
            result = await conn.execute(q, p)
            return result.rowcount

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        q, p = self._pg_sql(sql, params)
        async with self._engine.connect() as conn:
            result = await conn.execute(q, p)
            return [dict(zip(result.keys(), row)) for row in result.fetchall()]

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        rows = await self.fetch_all(sql, params)
        return list(rows[0].values())[0] if rows else None

    async def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run an INSERT and return its generated id via RETURNING."""
        row = await self.fetch_one(sql + " RETURNING id", params)
        return int(row["id"]) if row else 0

    async def last_insert_rowid(self) -> int:
        # PG uses RETURNING id; repos call insert() instead
        raise NotImplementedError("use insert() / RETURNING id on PostgreSQL")

    @asynccontextmanager
    async def tx(self):
        """Transaction scope: yields a raw connection for SKIP LOCKED claims."""
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            yield _PgTxProxy(conn, self._pg_sql)

    async def audit(self, actor: str, action: str, target: str = "", ip: str = "", details: str = "") -> None:
        await self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, ip, details) VALUES(?,?,?,?,?,?)",
            (_now(), actor, action, target, ip, details),
        )

    async def audit_row(self, action: str) -> Optional[dict]:
        row = await self.fetch_one("SELECT * FROM audit_log WHERE action=? ORDER BY id DESC LIMIT 1", (action,))
        return dict(row) if row else None



class MysqlDatabase:
    """MySQL / MariaDB backend with the same interface as Database.

    Activated when TGDRIVE_DATABASE_URL is a mysql:// / mariadb:// URL.
    SQL strings across the codebase use SQLite ``?`` placeholders; they are
    translated to SQLAlchemy named parameters transparently. Row results are
    plain dicts (matching aiosqlite Row -> dict conversion upstream).

    Differences from PostgreSQL worth knowing:
    - no ``RETURNING``: generated ids come from ``insert()`` (lastrowid)
    - ``ON CONFLICT`` is ``ON DUPLICATE KEY UPDATE`` (see settings_service)
    - ``key`` is a reserved word, so settings/system_meta quote it
    """

    is_sqlite = False
    is_mysql = True
    dialect = "mysql"

    def __init__(self, url: str) -> None:
        self.url = url
        self._engine = None

    @staticmethod
    def normalize_url(url: str) -> tuple:
        """mysql:// / mariadb:// URL -> SQLAlchemy async (aiomysql) URL."""
        if url.startswith("mysql://"):
            url = url.replace("mysql://", "mysql+aiomysql://", 1)
        elif url.startswith("mariadb://"):
            url = url.replace("mariadb://", "mysql+aiomysql://", 1)
        connect_args = {"charset": "utf8mb4", "connect_timeout": 10}
        return url, connect_args

    async def connect(self) -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine_url, connect_args = self.normalize_url(self.url)
        self._engine = create_async_engine(
            engine_url, pool_pre_ping=True, pool_size=10, max_overflow=20,
            connect_args=connect_args,
        )
        async with self._engine.begin() as conn:
            for stmt in _schema_statements(SCHEMA_MYSQL):
                await conn.execute(text(stmt))
        # one transaction per index: MySQL has no CREATE INDEX IF NOT EXISTS,
        # so an existing index raises 1061 and must not poison the batch
        for stmt in SCHEMA_MYSQL_INDEXES:
            try:
                async with self._engine.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception:
                pass

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    # ---------- SQL translation ----------
    @staticmethod
    def _mysql_sql(sql: str, params: Iterable[Any]) -> tuple[str, dict]:
        from sqlalchemy import text

        parts: list[str] = []
        names: list[str] = []
        idx = 0
        sql_out: list[str] = []
        i = 0
        while i < len(sql):
            ch = sql[i]
            if ch == "?":
                name = f"p{idx}"
                names.append(name)
                sql_out.append(f":{name}")
                idx += 1
            else:
                sql_out.append(ch)
            i += 1
        translated = "".join(sql_out)
        plist = list(params)
        if len(plist) != idx:
            raise ValueError(f"placeholder mismatch: {idx} params expected, {len(plist)} given: {translated[:120]}")
        return text(translated), {f"p{k}": v for k, v in enumerate(plist)}

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        q, p = self._mysql_sql(sql, params)
        async with self._engine.begin() as conn:
            result = await conn.execute(q, p)
            return result.rowcount

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        q, p = self._mysql_sql(sql, params)
        async with self._engine.connect() as conn:
            result = await conn.execute(q, p)
            return [dict(zip(result.keys(), row)) for row in result.fetchall()]

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        rows = await self.fetch_all(sql, params)
        return list(rows[0].values())[0] if rows else None

    async def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run an INSERT and return its generated id (MySQL has no RETURNING)."""
        q, p = self._mysql_sql(sql, params)
        async with self._engine.begin() as conn:
            result = await conn.execute(q, p)
            return int(result.lastrowid or 0)

    async def last_insert_rowid(self) -> int:
        # per-connection state: only correct right after an INSERT on a pooled
        # connection, so repos use insert() instead of execute()+this.
        return int(await self.scalar("SELECT LAST_INSERT_ID()") or 0)

    @asynccontextmanager
    async def tx(self):
        """Transaction scope: yields a proxy for SKIP LOCKED claims."""
        async with self._engine.begin() as conn:
            yield _MysqlTxProxy(conn, self._mysql_sql)

    async def audit(self, actor: str, action: str, target: str = "", ip: str = "", details: str = "") -> None:
        await self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, ip, details) VALUES(?,?,?,?,?,?)",
            (_now(), actor, action, target, ip, details),
        )

    async def audit_row(self, action: str) -> Optional[dict]:
        row = await self.fetch_one("SELECT * FROM audit_log WHERE action=? ORDER BY id DESC LIMIT 1", (action,))
        return dict(row) if row else None


class _MysqlTxProxy:
    """Minimal proxy handed to `async with db.tx()` blocks on MySQL."""

    def __init__(self, conn: Any, translator) -> None:
        self._conn = conn
        self._translate = translator

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        q, p = self._translate(sql, params)
        return await self._conn.execute(q, p)

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        q, p = self._translate(sql, params)
        result = await self._conn.execute(q, p)
        row = result.mappings().first()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> List[dict]:
        q, p = self._translate(sql, params)
        result = await self._conn.execute(q, p)
        return [dict(r) for r in result.mappings().all()]


class _PgTxProxy:
    """Minimal proxy handed to `async with db.tx()` blocks on PG."""

    def __init__(self, conn: Any, translator) -> None:
        self._conn = conn
        self._translate = translator

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        q, p = self._translate(sql, params)
        return await self._conn.execute(q, p)

    async def fetchone(self):
        result = getattr(self, "_last_result", None)
        return result.fetchone() if result else None
