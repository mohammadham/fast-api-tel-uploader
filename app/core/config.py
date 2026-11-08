"""TelegramDrive — application configuration via environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TGDRIVE_", env_file=".env", extra="ignore")

    # ── client IP trust ──
    trusted_proxies: str = ""  # comma-separated IPs/CIDRs; empty = trust nobody

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
    transfer_delete_source: bool = False  # after a successful transfer, delete the source copies
    upload_stall_threshold_s: int = 120  # upload queue waiting longer than this → admin stall alert
    fake_send_delay_s: int = 0           # fake-TG only: sleep per send (simulate slow telegram)
    flood_alert_threshold: int = 3       # consecutive flood-waits on one backend before an admin alert
    flood_alert_cooldown_s: int = 600    # min seconds between repeated flood alerts per backend
    bot_api_base: str = "https://api.telegram.org"
    bot_admin_ids: str = ""  # comma separated telegram user ids allowed to control the bot

    # ── queue / workers ──
    download_workers: int = 4
    upload_workers: int = 2
    #: per-account download slots (NOT divided by the pool size)
    max_concurrent_downloads: int = 4
    #: global upload gate (all accounts together)
    max_concurrent_uploads: int = 2
    #: per-account upload slots (separate from the global gate)
    max_concurrent_uploads_per_account: int = 2
    #: parallel SaveBigFilePart requests per uploaded file (MTProto only)
    upload_part_workers: int = 4
    #: gzip-compress uploaded parts when it actually makes them smaller
    compress_uploads: int = 0
    #: minimum percentage saved before a gzip part replaces the original
    compress_min_savings_pct: int = 5
    #: cache per-account chat access; downloads pick only an authorized account
    access_cache_enabled: int = 1
    access_cache_ttl_s: int = 6 * 3600
    access_cache_negative_ttl_s: int = 10 * 60
    access_probe_limit: int = 3
    # ── automatic multi-account split (separate from split_threshold) ──
    #: split a large upload across several free accounts instead of one account
    auto_split_enabled: int = 0
    #: only files at least this large are eligible (0 = no minimum)
    auto_split_min_file_size: int = 128 * 1024 * 1024
    #: never create a part smaller than this (except a tiny final safety edge)
    auto_split_min_part_size: int = 32 * 1024 * 1024
    #: keep every part below this; the hard split_threshold still wins
    auto_split_max_part_size: int = 512 * 1024 * 1024
    #: need at least this many free upload accounts before splitting is worthwhile
    auto_split_min_accounts: int = 2
    #: never use more than this many accounts for one file
    auto_split_max_accounts: int = 4
    #: safety ceiling for the number of parts of one file
    auto_split_max_parts: int = 64
    job_max_retries: int = 5
    job_lease_seconds: int = 1800

    # ── scheduler (size + age aware) ──
    #: uploads/transfers at or below this size use the "small" lane
    scheduler_small_max_bytes: int = 64 * 1024 * 1024
    #: a job waiting longer than this jumps ahead of every non-overdue job
    scheduler_max_wait_s: int = 900
    #: candidate window scanned per claim (bounded work per dispatch)
    scheduler_scan_limit: int = 200

    # ── admission control / disk safety ──
    max_queue_jobs: int = 1000          # 0 = unlimited
    max_inflight_bytes: int = 0         # 0 = unlimited; bytes of queued+running work
    disk_watermark_pct: int = 15         # reject uploads that would leave less free space than this %
    tmp_retention_hours: int = 2         # janitor keeps inactive tmp files this long

    # ── per-bot delivery limits (Bot API) ──
    bot_max_concurrent: int = 2
    bot_max_rpm: int = 60               # 0 = off

    # ── per-account Telegram request budget (0 = off) ──
    account_max_rpm: int = 0
    premium_weight: int = 2             # account selection weight for premium accounts

    # ── limits ──
    max_upload_size: int = 2_100_000_000  # ~2GB
    split_threshold: int = 1_900_000_000  # split parts below MTProto 2GB cap
    default_key_rpm: int = 120            # requests per minute per API key
    default_key_daily_quota: int = 100 * 1024 * 1024 * 1024  # 100GB/day
    #: concurrent download/preview streams allowed per API key / public link
    stream_max_per_key: int = 2
    #: Retry-After advertised when a stream is rejected by that cap
    stream_retry_after_s: int = 5
    blocked_extensions: str = ".exe,.bat,.cmd,.msi,.scr,.com"
    # thumbnails generated for media uploads (see app/services/thumbs.py)
    thumb_max_px: int = 320          # long side of the generated thumbnail
    thumb_quality: int = 80          # JPEG quality of the generated thumbnail

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
    database_url: str = ""  # postgres://… / mysql://… / mariadb://… (empty → local SQLite)
    db_engine: str = "sqlite"  # sqlite | postgres | mysql; persisted in system_meta
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
