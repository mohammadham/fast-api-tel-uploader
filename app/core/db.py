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
  last_used_at REAL
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
  error TEXT NOT NULL DEFAULT ''
);
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
CREATE INDEX IF NOT EXISTS idx_file_parts_file ON file_parts(file_id, idx);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


def _now() -> float:
    return time.time()


class Database:
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
            "ALTER TABLE files ADD COLUMN deleted_at REAL",
            "ALTER TABLE files ADD COLUMN thumb_message_id INTEGER",
            "ALTER TABLE files ADD COLUMN backend TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE api_keys ADD COLUMN backend TEXT NOT NULL DEFAULT ''",
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

    # ---------- audit ----------
    async def audit(self, actor: str, action: str, target: str = "", ip: str = "", details: str = "") -> None:
        await self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, ip, details) VALUES(?,?,?,?,?,?)",
            (_now(), actor, action, target, ip, details),
        )
