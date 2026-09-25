"""Typed models + repository helpers shared across services."""
from __future__ import annotations

import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .db import Database

# ── queue kinds & priorities (download-first policy) ──
KIND_DOWNLOAD = "download"
KIND_UPLOAD = "upload"
KIND_DELETE = "delete"

# ── audit actions (grep-able constants; used by panel + API) ──
AUDIT_LOGIN_OK = "auth.login.ok"
AUDIT_LOGIN_FAIL = "auth.login.fail"
AUDIT_LOGOUT = "auth.logout"
AUDIT_KEY_CREATE = "apikey.create"
AUDIT_KEY_REVOKE = "apikey.revoke"
AUDIT_ACCOUNT_ADD = "account.add"
AUDIT_ACCOUNT_DELETE = "account.delete"
AUDIT_ACCOUNT_RESET = "account.reset"
AUDIT_BOT_ADD = "bot.add"
AUDIT_BOT_DELETE = "bot.delete"
AUDIT_FILE_UPLOAD = "file.upload"
AUDIT_FILE_DELETE = "file.delete"
AUDIT_LINK_CREATE = "link.create"
AUDIT_QUEUE_CONTROL = "queue.control"

PRIO_DOWNLOAD = 100
PRIO_STREAM = 60
PRIO_UPLOAD = 40
PRIO_DELETE = 10


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def now() -> float:
    return time.time()


@dataclass
class Job:
    id: str
    kind: str
    priority: int
    seq: int
    correlation_id: str = ""
    status: str = "pending"
    payload: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_retries: int = 5
    next_run_at: float = 0.0
    lease_until: float = 0.0
    lease_owner: str = ""
    origin_node: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: Optional[float] = None
    error: str = ""


class UserRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_admin(self, username: str, password: str) -> None:
        row = await self.db.fetch_one("SELECT id FROM users WHERE username=?", (username,))
        if row:
            return
        from .security import hash_password

        await self.db.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?, 'admin', ?)",
            (username, hash_password(password), now()),
        )

    async def set_password(self, username: str, password: str) -> None:
        from .security import hash_password

        await self.db.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (hash_password(password), username),
        )

    async def get(self, username: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM users WHERE username=?", (username,))


class ApiKeyRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        name: str,
        raw_key: str,
        scopes: str = "read,write",
        rpm: int = 120,
        daily_quota_bytes: int = 0,
        expires_at: Optional[float] = None,
        backend: str = "",
        storage_chat: str = "",
    ) -> Dict[str, Any]:
        from .security import key_hash, key_prefix

        ts = now()
        await self.db.execute(
            "INSERT INTO api_keys(name, key_hash, key_prefix, scopes, rpm, daily_quota_bytes, expires_at, backend, storage_chat, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, key_hash(raw_key), key_prefix(raw_key), scopes, rpm, daily_quota_bytes, expires_at, backend, storage_chat, ts),
        )
        return {
            "name": name,
            "key": raw_key,  # only returned once
            "prefix": key_prefix(raw_key),
            "scopes": scopes,
            "rpm": rpm,
            "daily_quota_bytes": daily_quota_bytes,
            "expires_at": expires_at,
            "backend": backend,
            "storage_chat": storage_chat,
        }

    async def find_by_raw(self, raw: str) -> Optional[Dict[str, Any]]:
        from .security import key_hash

        row = await self.db.fetch_one("SELECT * FROM api_keys WHERE key_hash=?", (key_hash(raw),))
        if not row or row["revoked"]:
            return None
        if row["expires_at"] and row["expires_at"] < now():
            return None
        return row

    async def list(self) -> List[Dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT id, name, key_prefix, scopes, rpm, daily_quota_bytes, used_bytes_today, quota_day,"
            " expires_at, revoked, created_at, last_used_at, backend, storage_chat FROM api_keys ORDER BY id DESC"
        )
        return rows

    async def revoke(self, key_id: int) -> int:
        return await self.db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))

    async def update(
        self,
        key_id: int,
        *,
        rpm: Optional[int] = None,
        daily_quota_bytes: Optional[int] = None,
        scopes: Optional[str] = None,
        backend: Optional[str] = None,
        storage_chat: Optional[str] = None,
    ) -> None:
        row = await self.db.fetch_one(
            "SELECT rpm, daily_quota_bytes, scopes, backend, storage_chat FROM api_keys WHERE id=?", (key_id,)
        )
        if not row:
            return
        await self.db.execute(
            "UPDATE api_keys SET rpm=?, daily_quota_bytes=?, scopes=?, backend=?, storage_chat=? WHERE id=?",
            (
                rpm if rpm is not None else row["rpm"],
                daily_quota_bytes if daily_quota_bytes is not None else row["daily_quota_bytes"],
                scopes if scopes is not None else row["scopes"],
                backend if backend is not None else row["backend"],
                storage_chat if storage_chat is not None else row["storage_chat"],
                key_id,
            ),
        )

    async def touch(self, key_id: int) -> None:
        await self.db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now(), key_id))


class AccountRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, label: str, phone: str = "", session_enc: str = "", is_premium: bool = False) -> int:
        sql = (
            "INSERT INTO tg_accounts(label, phone, session_enc, is_premium, status, created_at)"
            " VALUES(?,?,?,?, 'pending', ?)"
        )
        params = (label, phone, session_enc, int(is_premium), now())
        if self.db.is_sqlite:
            await self.db.execute(sql, params)
            return await self.db.last_insert_rowid()
        row = await self.db.fetch_one(sql + " RETURNING id", params)
        return int(row["id"]) if row else 0

    async def list(self) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT id, label, phone, is_premium, enabled, storage_chat_id, status, flood_until,"
            " circuit_open_until, error_count, uploads_done, downloads_done, bytes_up, bytes_down,"
            " last_error, created_at FROM tg_accounts ORDER BY id"
        )

    async def get(self, account_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM tg_accounts WHERE id=?", (account_id,))

    async def set_session(self, account_id: int, session_enc: str, status: str) -> None:
        await self.db.execute(
            "UPDATE tg_accounts SET session_enc=?, status=? WHERE id=?", (session_enc, status, account_id)
        )

    async def set_enabled(self, account_id: int, enabled: bool) -> None:
        await self.db.execute("UPDATE tg_accounts SET enabled=? WHERE id=?", (int(enabled), account_id))

    async def delete(self, account_id: int) -> None:
        await self.db.execute("DELETE FROM tg_accounts WHERE id=?", (account_id,))

    async def mark_error(self, account_id: int, error: str, flood_until: float = 0.0) -> None:
        await self.db.execute(
            "UPDATE tg_accounts SET error_count=error_count+1, last_error=?, flood_until=? WHERE id=?",
            (error[:500], flood_until, account_id),
        )

    async def mark_success(self, account_id: int, up: int = 0, down: int = 0, bytes_up: int = 0, bytes_down: int = 0) -> None:
        await self.db.execute(
            "UPDATE tg_accounts SET error_count=0, status='ready', uploads_done=uploads_done+?, downloads_done=downloads_done+?,"
            " bytes_up=bytes_up+?, bytes_down=bytes_down+? WHERE id=?",
            (up, down, bytes_up, bytes_down, account_id),
        )

    async def set_circuit(self, account_id: int, until: float) -> None:
        await self.db.execute("UPDATE tg_accounts SET circuit_open_until=? WHERE id=?", (until, account_id))

    async def set_status(self, account_id: int, status: str, error: str = "") -> None:
        await self.db.execute(
            "UPDATE tg_accounts SET status=?, last_error=? WHERE id=?", (status, error[:500], account_id)
        )

    async def note_bytes(self, account_id: int, up: int = 0, down: int = 0) -> None:
        await self.db.execute(
            "UPDATE tg_accounts SET bytes_up=bytes_up+?, bytes_down=bytes_down+? WHERE id=?",
            (up, down, account_id),
        )

    async def enabled_ready(self) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT * FROM tg_accounts WHERE enabled=1 AND session_enc != '' AND status='ready'"
            " AND flood_until < ? AND circuit_open_until < ?",
            (now(), now()),
        )


class BotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, label: str, token_enc: str) -> int:
        sql = "INSERT INTO bot_tokens(label, token_enc, status, created_at) VALUES(?,?, 'pending', ?)"
        params = (label, token_enc, now())
        if self.db.is_sqlite:
            await self.db.execute(sql, params)
            return await self.db.last_insert_rowid()
        row = await self.db.fetch_one(sql + " RETURNING id", params)
        return int(row["id"]) if row else 0

    async def list(self) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT id, label, enabled, status, last_error, created_at FROM bot_tokens ORDER BY id"
        )

    async def get(self, bot_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM bot_tokens WHERE id=?", (bot_id,))

    async def set_enabled(self, bot_id: int, enabled: bool) -> None:
        await self.db.execute("UPDATE bot_tokens SET enabled=? WHERE id=?", (int(enabled), bot_id))

    async def set_status(self, bot_id: int, status: str, error: str = "") -> None:
        await self.db.execute("UPDATE bot_tokens SET status=?, last_error=? WHERE id=?", (status, error[:500], bot_id))

    async def delete(self, bot_id: int) -> None:
        await self.db.execute("DELETE FROM bot_tokens WHERE id=?", (bot_id,))


class EitaaAccountRepo:
    """Eitaayar API tokens (send-only; token from eitaayar.ir)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, label: str, token_enc: str, chat_id: str) -> int:
        sql = "INSERT INTO eitaa_accounts(label, token_enc, chat_id, status, created_at) VALUES(?,?,?, 'pending', ?)"
        params = (label, token_enc, chat_id, now())
        if self.db.is_sqlite:
            await self.db.execute(sql, params)
            return await self.db.last_insert_rowid()
        row = await self.db.fetch_one(sql + " RETURNING id", params)
        return int(row["id"]) if row else 0

    async def list(self) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT id, label, chat_id, enabled, status, last_error, created_at FROM eitaa_accounts ORDER BY id"
        )

    async def get(self, eitaa_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM eitaa_accounts WHERE id=?", (eitaa_id,))

    async def set_enabled(self, eitaa_id: int, enabled: bool) -> None:
        await self.db.execute("UPDATE eitaa_accounts SET enabled=? WHERE id=?", (int(enabled), eitaa_id))

    async def set_status(self, eitaa_id: int, status: str, error: str = "") -> None:
        await self.db.execute(
            "UPDATE eitaa_accounts SET status=?, last_error=? WHERE id=?", (status, error[:500], eitaa_id)
        )

    async def delete(self, eitaa_id: int) -> None:
        await self.db.execute("DELETE FROM eitaa_accounts WHERE id=?", (eitaa_id,))


class FolderRepo:
    """Virtual folders for organizing files (acts like tags with hierarchy).

    A folder is (name, parent_id). The root is parent_id IS NULL. Nested
    folders are supported: /projects/2026/reports resolves by walking parents;
    names are unique among siblings only (like a real filesystem).
    """

    MAX_DEPTH = 32

    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self) -> List[Dict[str, Any]]:
        """All folders + aggregated stats + full path of each node."""
        rows = await self.db.fetch_all(
            "SELECT id, name, parent_id, created_at FROM folders ORDER BY created_at, id"
        )
        stats = await self.db.fetch_all(
            "SELECT COALESCE(folder_id, 0) AS fid, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes"
            " FROM files WHERE deleted_at IS NULL GROUP BY 1"
        )
        by_id = {r["id"]: r for r in rows}
        stat_map = {int(r["fid"]): (int(r["n"]), int(r["bytes"])) for r in stats}
        out = []
        for r in rows:
            # resolve full path by walking up (guard against cycles)
            path_parts = []
            cur = r
            seen = set()
            while cur is not None and cur["id"] not in seen:
                seen.add(cur["id"])
                path_parts.append(cur["name"])
                cur = by_id.get(cur["parent_id"])
            n, total = stat_map.get(r["id"], (0, 0))
            out.append({
                "id": r["id"], "name": r["name"], "parent_id": r["parent_id"],
                "path": "/".join(reversed(path_parts)),
                "created_at": r["created_at"],
                "file_count": n, "total_size": total,
            })
        return out

    async def get(self, folder_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT id, name, parent_id, created_at FROM folders WHERE id=?", (folder_id,))

    async def find_child(self, parent_id: Optional[int], name: str) -> Optional[Dict[str, Any]]:
        if parent_id is None:
            return await self.db.fetch_one("SELECT id, name, parent_id, created_at FROM folders WHERE parent_id IS NULL AND name=?", (name,))
        return await self.db.fetch_one("SELECT id, name, parent_id, created_at FROM folders WHERE parent_id=? AND name=?", (parent_id, name))

    async def create(self, name: str, parent_id: Optional[int] = None) -> int:
        await self.db.execute(
            "INSERT INTO folders(name, parent_id, created_at) VALUES(?,?,?)",
            (name, parent_id, now()),
        )
        return await self.db.last_insert_rowid()

    async def rename(self, folder_id: int, name: str) -> None:
        await self.db.execute("UPDATE folders SET name=? WHERE id=?", (name, folder_id))

    async def move(self, folder_id: int, parent_id: Optional[int]) -> None:
        await self.db.execute("UPDATE folders SET parent_id=? WHERE id=?", (parent_id, folder_id))

    async def delete(self, folder_id: int) -> int:
        """Delete folder; files inside fall back to no folder (NOT recursive)."""
        return await self.db.execute("DELETE FROM folders WHERE id=?", (folder_id,))

    async def subtree_ids(self, folder_id: int) -> List[int]:
        """BFS over children; guards against cycles via visited set."""
        rows = await self.db.fetch_all("SELECT id, parent_id FROM folders")
        children: Dict[Optional[int], List[int]] = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])
        out, stack, seen = [], [folder_id], set()
        while stack:
            fid = stack.pop()
            if fid in seen:
                continue
            seen.add(fid)
            out.append(fid)
            stack.extend(children.get(fid, []))
        return out

    async def path_of(self, folder_id: Optional[int]) -> str:
        if not folder_id:
            return ""
        by_id = {r["id"]: r for r in await self.db.fetch_all("SELECT id, name, parent_id FROM folders")}
        parts, cur, seen = [], by_id.get(folder_id), set()
        while cur is not None and cur["id"] not in seen:
            seen.add(cur["id"])
            parts.append(cur["name"])
            cur = by_id.get(cur["parent_id"])
        return "/".join(reversed(parts))

    async def resolve_path(self, path: str, *, create: bool = False) -> Optional[int]:
        """'/a/b/c' → id of c; '' or '/' → None (root). Creates missing levels when create=True."""
        parts = [p for p in (path or "").strip().strip("/").split("/") if p]
        if not parts:
            return None
        parent: Optional[int] = None
        for depth, raw in enumerate(parts):
            name = (raw or "").strip()
            if not name or len(name) > 100 or depth >= self.MAX_DEPTH:
                raise ValueError("invalid folder path")
            row = await self.find_child(parent, name)
            if row:
                parent = row["id"]
            elif create:
                parent = await self.create(name, parent)
            else:
                return None
        return parent


class FileRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        file_id: str,
        name: str,
        size: int,
        mime: str = "application/octet-stream",
        uploader: str = "api",
        source: str = "api",
        backend: str = "",
        folder_id: Optional[int] = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO files(id, name, size, mime, uploader, source, backend, folder_id, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?, 'queued', ?)",
            (file_id, name, size, mime, uploader, source, backend, folder_id, now()),
        )

    async def get(self, file_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM files WHERE id=?", (file_id,))

    async def list(
        self,
        limit: int = 100,
        offset: int = 0,
        folder_id: Optional[int] = None,
        include_subfolders: bool = False,
        storage_chat: Optional[str] = None,
        storage_chat_is_default: bool = False,
        q: str = "",
        mime_prefix: str = "",
        order: str = "date",
        blocked_only: bool = False,
    ) -> List[Dict[str, Any]]:
        cols = (
            "SELECT id, name, size, mime, status, parts, downloads, bytes_served, uploader, source,"
            " backend, storage_chat, folder_id, blocked, created_at, ready_at, error FROM files"
        )
        params: list = []
        prefix, conds = "", ["deleted_at IS NULL"]
        if q:
            conds.append("name LIKE ?")
            params.append(f"%{q}%")
        if mime_prefix:
            conds.append("mime LIKE ?")
            params.append(f"{mime_prefix}%")
        if blocked_only:
            conds.append("blocked=1")
        if order == "size":
            order_sql = " ORDER BY size DESC"
        elif order == "downloads":
            order_sql = " ORDER BY downloads DESC"
        elif order == "name":
            order_sql = " ORDER BY name COLLATE NOCASE"
        else:
            order_sql = " ORDER BY created_at DESC"
        if folder_id is not None:
            params.append(folder_id)
            if include_subfolders:
                # recursive CTE keeps this single-query and index friendly
                prefix = (
                    "WITH folder_tree(id) AS ("
                    " SELECT id FROM folders WHERE id=?"
                    " UNION ALL SELECT f.id FROM folders f JOIN folder_tree t ON f.parent_id=t.id) "
                )
                conds.append("folder_id IN (SELECT id FROM folder_tree)")
            else:
                conds.append("folder_id=?")
        if storage_chat is not None:
            params.append(storage_chat)
            if storage_chat_is_default:
                # files saved before a default was configured carry '' and resolve
                # to the system default chat — both belong to that channel view
                conds.append("(storage_chat='' OR storage_chat=?)")
            else:
                conds.append("storage_chat=?")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        sql = prefix + cols + where + order_sql + " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self.db.fetch_all(sql, params)

    async def count(
        self,
        *,
        folder_id: Optional[int] = None,
        include_subfolders: bool = False,
        storage_chat: Optional[str] = None,
        storage_chat_is_default: bool = False,
        q: str = "",
        mime_prefix: str = "",
        blocked_only: bool = False,
    ) -> int:
        """Same filter set as list() but returns the total row count."""
        params: list = []
        prefix, conds = "", ["deleted_at IS NULL"]
        if q:
            conds.append("name LIKE ?")
            params.append(f"%{q}%")
        if mime_prefix:
            conds.append("mime LIKE ?")
            params.append(f"{mime_prefix}%")
        if blocked_only:
            conds.append("blocked=1")
        if folder_id is not None:
            params.append(folder_id)
            if include_subfolders:
                prefix = (
                    "WITH folder_tree(id) AS ("
                    " SELECT id FROM folders WHERE id=?"
                    " UNION ALL SELECT f.id FROM folders f JOIN folder_tree t ON f.parent_id=t.id) "
                )
                conds.append("folder_id IN (SELECT id FROM folder_tree)")
            else:
                conds.append("folder_id=?")
        if storage_chat is not None:
            params.append(storage_chat)
            if storage_chat_is_default:
                conds.append("(storage_chat='' OR storage_chat=?)")
            else:
                conds.append("storage_chat=?")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        sql = prefix + "SELECT COUNT(*) FROM files" + where
        return int(await self.db.scalar(sql, params) or 0)

    async def set_blocked(self, file_id: str, blocked: bool) -> None:
        await self.db.execute("UPDATE files SET blocked=? WHERE id=?", (1 if blocked else 0, file_id))

    async def set_status(self, file_id: str, status: str, error: str = "") -> None:
        extra = ""
        params: list = [status, error[:500]]
        if status == "ready":
            extra = ", ready_at=?"
            params.append(now())
        params.append(file_id)
        await self.db.execute(f"UPDATE files SET status=?, error=?{extra} WHERE id=?", params)

    async def set_stored(self, file_id: str, storage_chat: str, message_ids: List[int], parts: int) -> None:
        await self.db.execute(
            "UPDATE files SET storage_chat=?, message_ids=?, parts=?, status='ready', ready_at=? WHERE id=?",
            (storage_chat, json.dumps(message_ids), parts, now(), file_id),
        )

    async def add_part(self, file_id: str, idx: int, message_id: int, size: int) -> None:
        await self.db.execute(
            "INSERT INTO file_parts(file_id, idx, message_id, size) VALUES(?,?,?,?)",
            (file_id, idx, message_id, size),
        )

    async def parts(self, file_id: str) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT idx, message_id, size FROM file_parts WHERE file_id=? ORDER BY idx", (file_id,)
        )

    async def count_download(self, file_id: str, nbytes: int) -> None:
        await self.db.execute(
            "UPDATE files SET downloads=downloads+1, bytes_served=bytes_served+? WHERE id=?",
            (nbytes, file_id),
        )

    async def delete(self, file_id: str) -> None:
        await self.db.execute("DELETE FROM file_parts WHERE file_id=?", (file_id,))
        await self.db.execute("DELETE FROM links WHERE file_id=?", (file_id,))
        await self.db.execute("DELETE FROM files WHERE id=?", (file_id,))

    async def soft_delete(self, file_id: str) -> int:
        return await self.db.execute("UPDATE files SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (now(), file_id))

    async def restore(self, file_id: str) -> int:
        return await self.db.execute("UPDATE files SET deleted_at=NULL WHERE id=?", (file_id,))

    async def trashed(self, older_than: float) -> List[Dict[str, Any]]:
        return await self.db.fetch_all("SELECT id, name FROM files WHERE deleted_at IS NOT NULL AND deleted_at < ?", (older_than,))

    async def set_thumb(self, file_id: str, message_id: int) -> None:
        await self.db.execute("UPDATE files SET thumb_message_id=? WHERE id=?", (message_id, file_id))

    async def set_folder(self, file_id: str, folder_id: Optional[int]) -> None:
        await self.db.execute("UPDATE files SET folder_id=? WHERE id=?", (folder_id, file_id))


class JobRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def insert(self, job: Job) -> None:
        from .obs import correlation_id

        await self.db.execute(
            "INSERT INTO jobs(id, kind, priority, seq, correlation_id, status, payload, attempts, max_retries,"
            " next_run_at, lease_until, lease_owner, origin_node, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.id,
                job.kind,
                job.priority,
                job.seq,
                job.correlation_id or correlation_id.get(""),
                job.status,
                json.dumps(job.payload),
                job.attempts,
                job.max_retries,
                job.next_run_at,
                job.lease_until,
                job.lease_owner,
                job.origin_node,
                job.created_at or now(),
                now(),
            ),
        )

    async def update_fields(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols: List[str] = []
        params: List[Any] = []
        for key, value in fields.items():
            cols.append(f"{key}=?")
            params.append(json.dumps(value) if key == "payload" and isinstance(value, dict) else value)
        cols.append("updated_at=?")
        params.append(now())
        params.append(job_id)
        await self.db.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", params)

    async def fetch(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    async def pending_snapshot(self) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT * FROM jobs WHERE status IN ('pending','running','lease') ORDER BY priority DESC, seq LIMIT 500"
        )

    async def stats(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for status in ("pending", "running", "done", "failed", "retry"):
            out[status] = int(
                await self.db.scalar("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)) or 0
            )
        out["by_kind"] = {}
        for kind in (KIND_DOWNLOAD, KIND_UPLOAD, KIND_DELETE):
            out["by_kind"][kind] = {
                s: int(
                    await self.db.scalar(
                        "SELECT COUNT(*) FROM jobs WHERE kind=? AND status=?", (kind, s)
                    )
                    or 0
                )
                for s in ("pending", "running", "done", "failed")
            }
        return out

    async def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT id, kind, priority, seq, status, attempts, correlation_id, created_at, updated_at, finished_at, error"
            " FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def purge_finished(self, older_than: float) -> int:
        return await self.db.execute(
            "DELETE FROM jobs WHERE status IN ('done','failed') AND finished_at < ?", (older_than,)
        )

    async def renew_leases(self, job_ids: List[str], owner: str, ttl: float) -> None:
        """Extend lease_until for jobs still running on this node."""
        if not job_ids:
            return
        ph = ",".join("?" for _ in job_ids)
        await self.db.execute(
            f"UPDATE jobs SET lease_until=?, updated_at=? WHERE id IN ({ph}) AND lease_owner LIKE ?",
            (now() + ttl, now(), *job_ids, f"{owner}%"),
        )


class UploadSessionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, session_id: str, name: str, size: int, mime: str, folder_path: str = "") -> None:
        await self.db.execute(
            "INSERT INTO upload_sessions(id, name, size, mime, offset, folder_path, created_at) VALUES(?,?,?,?,0,?,?)",
            (session_id, name, size, mime, folder_path, now()),
        )

    async def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM upload_sessions WHERE id=?", (session_id,))

    async def advance(self, session_id: str, offset: int) -> None:
        await self.db.execute("UPDATE upload_sessions SET offset=? WHERE id=?", (offset, session_id))

    async def delete(self, session_id: str) -> None:
        await self.db.execute("DELETE FROM upload_sessions WHERE id=?", (session_id,))

    async def stale(self, ttl_seconds: float) -> List[Dict[str, Any]]:
        return await self.db.fetch_all("SELECT * FROM upload_sessions WHERE created_at < ?", (now() - ttl_seconds,))


class AuditRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, limit: int = 200) -> List[Dict[str, Any]]:
        return await self.db.fetch_all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


class ProxyRepo:
    """Telegram proxy pool (MTProto / SOCKS5 / HTTP) with speed-test state."""

    VALID_KINDS = ("mtproto", "socks5", "http")

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        host: str,
        port: int,
        kind: str = "socks5",
        label: str = "",
        username: str = "",
        password: str = "",
        secret_hex: str = "",
    ) -> int:
        from .security import encrypt_str

        kind = kind.strip().lower()
        if kind not in self.VALID_KINDS:
            raise ValueError(f"unsupported proxy kind: {kind}")
        await self.db.execute(
            "INSERT INTO proxies(label, kind, host, port, username, password_enc, secret_hex, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (label[:100], kind, host.strip(), int(port), username, encrypt_str(password) if password else "", secret_hex.strip().lower(), now()),
        )
        if self.db.is_sqlite:
            return await self.db.last_insert_rowid()
        row = await self.db.fetch_one(
            "SELECT id FROM proxies WHERE host=? AND port=? AND kind=? ORDER BY id DESC LIMIT 1",
            (host.strip(), int(port), kind),
        )
        return int(row["id"]) if row else 0

    async def list(self, only_enabled: bool = False) -> List[Dict[str, Any]]:
        sql = (
            "SELECT id, label, kind, host, port, username, secret_hex, enabled, status, latency_ms,"
            " last_checked_at, last_error, created_at FROM proxies"
        )
        if only_enabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY CASE WHEN latency_ms < 0 THEN 1 ELSE 0 END, latency_ms ASC, id ASC"
        return await self.db.fetch_all(sql)

    async def list_enabled_sorted(self) -> List[Dict[str, Any]]:
        return await self.list(only_enabled=True)

    async def get(self, proxy_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM proxies WHERE id=?", (proxy_id,))

    async def set_enabled(self, proxy_id: int, enabled: bool) -> int:
        return await self.db.execute("UPDATE proxies SET enabled=? WHERE id=?", (int(enabled), proxy_id))

    async def delete(self, proxy_id: int) -> int:
        return await self.db.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))

    async def set_check_result(self, proxy_id: int, status: str, latency_ms: float, error: str = "") -> None:
        await self.db.execute(
            "UPDATE proxies SET status=?, latency_ms=?, last_checked_at=?, last_error=? WHERE id=?",
            (status, float(latency_ms), now(), error[:300], proxy_id),
        )

    async def clear_all_results(self) -> int:
        return await self.db.execute(
            "UPDATE proxies SET status='unknown', latency_ms=-1, last_checked_at=0, last_error='' WHERE 1=1", ()
        )

    async def count(self) -> int:
        return int(await self.db.scalar("SELECT COUNT(*) FROM proxies") or 0)


class LinkRepo:
    """Public share links: short slug, optional password, optional download cap."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, file_id: str, slug: str, pwd_hash: str = "", max_downloads: int = 0) -> int:
        await self.db.execute(
            "INSERT INTO links(file_id, slug, pwd_hash, max_downloads, created_at) VALUES(?,?,?,?,?)",
            (file_id, slug, pwd_hash, max_downloads, now()),
        )
        return int(await self.db.scalar("SELECT id FROM links WHERE slug=?", (slug,)) or 0)

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return await self.db.fetch_one("SELECT * FROM links WHERE slug=?", (slug,))

    async def list_for_file(self, file_id: str) -> List[Dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT id, slug, max_downloads, hits, disabled, created_at FROM links WHERE file_id=? ORDER BY id DESC",
            (file_id,),
        )

    async def count_hit(self, link_id: int) -> None:
        await self.db.execute("UPDATE links SET hits=hits+1 WHERE id=?", (link_id,))

    async def delete(self, link_id: int) -> int:
        return await self.db.execute("DELETE FROM links WHERE id=?", (link_id,))

    async def set_disabled(self, link_id: int, disabled: bool) -> int:
        return await self.db.execute("UPDATE links SET disabled=? WHERE id=?", (1 if disabled else 0, link_id))
