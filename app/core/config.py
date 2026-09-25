"""TelegramDrive — application configuration via environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TGDRIVE_", env_file=".env", extra="ignore")

    # ── security ──
    secret: str = "dev-secret-change-me-please-32-chars!"
    jwt_ttl_minutes: int = 15
    refresh_ttl_days: int = 14
    presigned_ttl: int = 3600
    fernet_key: str = ""  # empty → derived from secret

    # ── admin bootstrap ──
    admin_username: str = "admin"
    admin_password: str = ""  # empty → generated on first start

    # ── telegram ──
    fake_tg: bool = False  # 1 → use in-memory fake (tests/dev)
    default_backend: str = "telegram"  # default storage destination: telegram | eitaa
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_storage_chat: str = ""  # global storage channel (@username or id); empty → per-account / Saved Messages
    # note: env file key is TGDRIVE_TG_STORAGE_CHAT (pydantic env_prefix maps it)
    bot_api_base: str = "https://api.telegram.org"
    bot_admin_ids: str = ""  # comma separated telegram user ids allowed to control the bot

    # ── queue / workers ──
    download_workers: int = 4
    upload_workers: int = 2
    max_concurrent_downloads: int = 4
    max_concurrent_uploads: int = 2
    job_max_retries: int = 5

    # ── limits ──
    max_upload_size: int = 2_100_000_000  # ~2GB
    split_threshold: int = 1_900_000_000  # split parts below MTProto 2GB cap
    default_key_rpm: int = 120            # requests per minute per API key
    default_key_daily_quota: int = 100 * 1024 * 1024 * 1024  # 100GB/day
    blocked_extensions: str = ".exe,.bat,.cmd,.msi,.scr,.com"

    # ── web ──
    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR (JSON logs)
    cors_origins: str = "*"
    data_dir: str = "data"
    db_path: str = ""  # empty → data_dir/telegramdrive.db
    upload_tmp_dir: str = ""  # empty → data_dir/tmp

    # ── upload session ──
    upload_session_ttl_minutes: int = 60

    # ── redis / queue scaling ──
    redis_url: str = ""  # e.g. redis://localhost:6379/0 (empty → SQLite only)
    redis_enabled: bool = False

    # ── external database / multi-server ──
    database_url: str = ""  # postgres://… (empty → local SQLite); MySQL not supported
    node_id: str = ""  # unique per server in multi-node deploys (default: hostname)

    @property
    def blocked_ext_set(self) -> set[str]:
        return {e.strip().lower() for e in self.blocked_extensions.split(",") if e.strip()}

    @property
    def bot_admin_id_set(self) -> set[int]:
        out: set[int] = set()
        for part in self.bot_admin_ids.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    def final_db_path(self) -> str:
        return self.db_path or f"{self.data_dir}/telegramdrive.db"

    def final_tmp_dir(self) -> str:
        return self.upload_tmp_dir or f"{self.data_dir}/tmp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
