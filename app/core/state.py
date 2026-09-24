"""Application state container shared across routers."""
from __future__ import annotations

from typing import Optional

from .config import get_settings
from .db import Database, PgDatabase


class AppState:
    def __init__(self) -> None:
        self.db: Optional[Database] = None
        self.manager = None
        self.queue = None
        self.bots = None
        self.janitor = None
        self.node_id: str = ""

    async def db_instance(self) -> Database:
        if self.db is None:
            s = get_settings()
            if s.database_url and s.database_url.startswith(("postgres://", "postgresql://")):
                db = PgDatabase(s.database_url)
            else:
                db = Database(s.final_db_path())
            await db.connect()
            self.db = db
            self.node_id = s.node_id or __import__("socket").gethostname()
        return self.db


state = AppState()


async def get_db() -> Database:
    return await state.db_instance()
