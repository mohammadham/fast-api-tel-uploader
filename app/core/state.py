"""Application state container shared across routers."""
from __future__ import annotations

import logging
from typing import Optional

from .config import get_settings
from .db import Database, MysqlDatabase, PgDatabase

log = logging.getLogger("tgdrive.state")

# last DB bootstrap error (setup/status surfaces it to the wizard)
db_error: str = ""
db_engine: str = ""  # "sqlite" | "postgres" | "mysql" (resolved engine in use)


class AppState:
    def __init__(self) -> None:
        self.db: Optional[Database] = None
        self.manager = None
        self.queue = None
        self.bots = None
        self.janitor = None
        self.proxy_monitor = None
        self.node_id: str = ""

    def _postgres_url(self) -> str:
        """Resolve the configured PG URL; empty when unset or forced sqlite."""
        s = get_settings()
        url = (s.database_url or "").strip()
        if not url:
            return ""
        # wizard choice (system_meta, seeded from env TGDRIVE_DB_ENGINE) can
        # force sqlite even when a PG URL is present in .env
        from .settings_service import get_meta_sync_hint
        if get_meta_sync_hint() == "sqlite":
            return ""
        return url if url.startswith(("postgres://", "postgresql://")) else ""

    def _mysql_url(self) -> str:
        """Resolve the configured MySQL / MariaDB URL."""
        s = get_settings()
        url = (s.database_url or "").strip()
        if not url:
            return ""
        # Only use mysql engine if explicitly set and not forced to sqlite
        from .settings_service import get_meta_sync_hint
        if get_meta_sync_hint() == "sqlite":
            return ""
        if url.startswith(("mysql://", "mariadb://")):
            return url
        return ""

    async def db_instance(self) -> Database:
        global db_error, db_engine
        if self.db is None:
            s = get_settings()
            pg_url = self._postgres_url()
            mysql_url = self._mysql_url()
            db = None
            if pg_url:
                try:
                    candidate = PgDatabase(pg_url)
                    await candidate.connect()
                    db, db_engine = candidate, "postgres"
                except Exception as exc:
                    # PG configured but unreachable/unusable → fall through to
                    # MySQL / SQLite so the service (and the wizard) still work
                    db_error = str(exc)[:400]
                    log.error("postgres unavailable (%s) → trying %s",
                              db_error, "MySQL" if mysql_url else "SQLite")
            if db is None and mysql_url:
                try:
                    candidate = MysqlDatabase(mysql_url)
                    await candidate.connect()
                    db, db_engine = candidate, "mysql"
                except Exception as exc:
                    # same policy as PG: never leave the service without a DB
                    db_error = str(exc)[:400]
                    log.error("mysql unavailable (%s) → falling back to SQLite at %s",
                              db_error, s.final_db_path())
            if db is None:
                db = Database(s.final_db_path())
                await db.connect()
                db_engine = "sqlite"
            self.db = db
            self.node_id = s.node_id or __import__("socket").gethostname()
        return self.db


state = AppState()


async def get_db() -> Database:
    return await state.db_instance()
