"""Application state container shared across routers."""
from __future__ import annotations

from typing import Optional

from .config import get_settings
from .db import Database


class AppState:
    def __init__(self) -> None:
        self.db: Optional[Database] = None
        self.manager = None
        self.queue = None
        self.bots = None
        self.janitor = None

    async def db_instance(self) -> Database:
        if self.db is None:
            db = Database(get_settings().final_db_path())
            await db.connect()
            self.db = db
        return self.db


state = AppState()


async def get_db() -> Database:
    return await state.db_instance()
