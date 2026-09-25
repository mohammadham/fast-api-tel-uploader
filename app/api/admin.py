"""Admin/dashboard endpoints: overview stats, audit log, health, metrics,
runtime settings, setup wizard, node registry, backup/restore."""
from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import os
import time
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.metrics import metrics
from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/overview")
async def overview(_: str = Depends(get_current_admin), db=Depends(get_db)):
    files_total = await db.scalar("SELECT COUNT(*) FROM files") or 0
    files_ready = await db.scalar("SELECT COUNT(*) FROM files WHERE status='ready'") or 0
    bytes_stored = await db.scalar("SELECT COALESCE(SUM(size),0) FROM files WHERE status='ready'") or 0
    bytes_served = await db.scalar("SELECT COALESCE(SUM(bytes_served),0) FROM files") or 0
    downloads = await db.scalar("SELECT COALESCE(SUM(downloads),0) FROM files") or 0
    accounts = await db.scalar("SELECT COUNT(*) FROM tg_accounts") or 0
    accounts_ready = await db.scalar("SELECT COUNT(*) FROM tg_accounts WHERE status='ready' AND enabled=1") or 0
    bots = await db.scalar("SELECT COUNT(*) FROM bot_tokens") or 0
    keys = await db.scalar("SELECT COUNT(*) FROM api_keys WHERE revoked=0") or 0
    nodes = await db.scalar("SELECT COUNT(*) FROM nodes") or 0
    qstats = await state.queue.stats() if state.queue else {}
    return {
        "files": {"total": files_total, "ready": files_ready, "bytes_stored": bytes_stored},
        "traffic": {"bytes_served": bytes_served, "downloads": downloads},
        "accounts": {"total": accounts, "ready": accounts_ready},
        "bots": bots,
        "api_keys": keys,
        "nodes": nodes,
        "queue": qstats,
        "uptime": round(time.time() - metrics._start, 1),
        "proxies": await _proxy_health(db),
    }


async def _proxy_health(db) -> dict:
    """Dashboard proxy-health summary: alive/dead counts + last fallback event.

    Counts come from the persisted speed-test state; dead marks from the
    in-process selector (fallback transport failures); the last fallback event
    is the newest audit row for a proxy marked dead by TGManager.
    """
    from ..services.proxy_service import selector as proxy_selector

    rows = await db.fetch_all(
        "SELECT id, label, host, port, status, latency_ms, last_checked_at FROM proxies"
    )
    total = len(rows)
    alive = sum(1 for r in rows if r["status"] in ("ok", "degraded"))
    down = sum(1 for r in rows if r["status"] == "down")
    unknown = sum(1 for r in rows if r["status"] == "unknown")
    dead_marks = proxy_selector._dead_now()  # recently reported dead via fallback
    best = min(
        (r["latency_ms"] for r in rows if r["status"] == "ok" and r["latency_ms"] >= 0),
        default=-1,
    )

    enabled = False
    try:
        from ..core.settings_service import get_runtime

        enabled = bool(int(await get_runtime(db, "proxy_enabled") or 0))
    except Exception:
        pass

    last_fallback = None
    try:
        row = await db.audit_row("proxy.fallback")
        if row:
            last_fallback = {"at": float(row["ts"]), "detail": (row["target"] or row["details"] or "").strip()}
    except Exception:
        pass
    fallback_count = int(metrics._counters.get("backends.proxy_fallback", 0))
    return {
        "enabled": enabled,
        "total": total,
        "alive": alive,
        "down": down,
        "unknown": unknown,
        "dead_marked": len(dead_marks),
        "best_latency_ms": round(best, 1) if best >= 0 else -1,
        "fallback_count": fallback_count,
        "last_fallback": last_fallback,
    }


@router.get("/audit")
async def audit(limit: int = 200, _: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import AuditRepo

    return {"items": await AuditRepo(db).list(min(limit, 1000))}


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus(_: str = Depends(get_current_admin)):
    # gauge refresh from live state
    if state.queue:
        q = await state.queue.stats()
        metrics.set_gauge("queue.pending", q.get("pending", 0))
        metrics.set_gauge("queue.running", q.get("running", 0))
    if state.manager:
        h = state.manager.health()
        metrics.set_gauge("backends.healthy", sum(1 for x in h if x["available"]))
    return metrics.render()


@router.get("/healthz")
async def healthz(db=Depends(get_db)):
    await db.scalar("SELECT 1")
    return {"ok": True}


@router.get("/readyz")
async def readyz(db=Depends(get_db)):
    ok = bool(state.queue) and bool(state.manager)
    if not ok:
        return PlainTextResponse('{"ok": false}', status_code=503, media_type="application/json")
    return {"ok": True}


# ── backup / restore (rewritten: old version used nonexistent db.acquire/cursor_description) ──
_BACKUP_TABLES = ("files", "file_parts", "tg_accounts", "bot_tokens", "eitaa_accounts", "api_keys", "links")


@router.get("/backup")
async def backup(_: str = Depends(get_current_admin), db=Depends(get_db)):
    """Table-rows JSON backup (no encrypted secrets for safety: tokens/sessions excluded)."""
    dump: dict[str, list] = {}
    for table in _BACKUP_TABLES:
        cols = await db.fetch_all(f"PRAGMA table_info({table})") if db.is_sqlite else []
        cols = [c["name"] for c in cols] if cols else []
        rows = await db.fetch_all(f"SELECT * FROM {table}")
        if cols:
            # drop encrypted columns — they are node-specific secrets, not portable
            drop = {"session_enc", "token_enc", "pwd_hash"}
            keep = [c for c in cols if c not in drop]
            dump[table] = [{k: r.get(k) for k in keep} for r in rows]
        else:
            dump[table] = rows
    payload = json.dumps({"ts": time.time(), "tables": dump}).encode()
    return {
        "ts": time.time(),
        "tables": {k: len(v) for k, v in dump.items()},
        "data": base64.b64encode(gzip.compress(payload)).decode(),
    }


class RestoreIn(BaseModel):
    data: str  # base64 gzip JSON from /backup


@router.post("/restore")
async def restore(body: RestoreIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    try:
        payload = json.loads(gzip.decompress(base64.b64decode(body.data)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid backup payload: {exc}")
    tables = payload.get("tables") or {}
    counts: dict[str, int] = {}
    for table, rows in tables.items():
        if table not in _BACKUP_TABLES or not isinstance(rows, list) or not rows:
            continue
        for row in rows:
            cols = list(row.keys())
            ph = ",".join("?" if db.is_sqlite else f":{c}" for c in cols)
            names = ",".join(cols)
            params = tuple(row[c] for c in cols) if db.is_sqlite else {c: row[c] for c in cols}
            try:
                if db.is_sqlite:
                    await db.execute(
                        f"INSERT OR IGNORE INTO {table}({names}) VALUES({ph})", params
                    )
                else:
                    await db.execute(
                        f"INSERT INTO {table}({names}) VALUES({ph}) ON CONFLICT DO NOTHING", params
                    )
            except Exception:
                continue  # skip rows that collide or miss required columns
        counts[table] = len(rows)
    await db.audit(admin, "admin.restore", details=json.dumps(counts)[:300])
    return {"ok": True, "restored": counts}


# ── runtime settings (DB-backed, admin-managed) ──────────────────
@router.get("/settings")
async def get_settings_api(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.settings_service import editable_settings_meta

    return {"items": await editable_settings_meta(db)}


@router.put("/settings")
async def put_settings(
    body: dict,
    admin: str = Depends(get_current_admin),
    db=Depends(get_db),
):
    from ..core.settings_service import save_runtime_settings

    try:
        applied = await save_runtime_settings(db, body, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "applied": applied}


@router.post("/settings/reset")
async def reset_settings(admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.settings_service import reset_runtime_settings

    await reset_runtime_settings(db, actor=admin)
    return {"ok": True}


# ── setup wizard: .env provisioning ──────────────────────────
@router.get("/setup/env")
async def setup_env_get(_: str = Depends(get_current_admin)):
    """Bootstrap-key status for the wizard (secrets masked, never returned)."""
    from ..services import env_service

    return env_service.env_status()


class SetupEnvIn(BaseModel):
    values: dict[str, str] = {}
    generate_secret: bool = False  # auto-fill TGDRIVE_SECRET when unset


@router.put("/setup/env")
async def setup_env_put(body: SetupEnvIn, admin: str = Depends(get_current_admin)):
    """Write bootstrap values into .env (creates the file when missing).

    Applies only keys the admin actually sent; TGDRIVE_SECRET can be
    auto-generated. Process env wins until restart, so the response reminds
    the admin which keys need a restart to take effect.
    """
    from ..services import env_service

    values = dict(body.values or {})
    if body.generate_secret:
        current = (os.environ.get("TGDRIVE_SECRET") or "").strip() or env_service.read_env_file().get("TGDRIVE_SECRET", "").strip()
        if not current:
            values.setdefault("TGDRIVE_SECRET", env_service.generate_secret())
    try:
        result = env_service.write_env_values(values, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # which of the written keys are shadowed by process env until restart?
    stale = [k for k in result["written"] if (os.environ.get(k) or "").strip()]
    return {**result, "restart_keys": stale}


# ── setup wizard (first-run flag in system_meta) ─────────────────
@router.get("/setup/status")
async def setup_status(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core import state as app_state
    from ..core.settings_service import get_meta

    s = get_settings()
    pg_configured = bool((s.database_url or "").strip().startswith(("postgres://", "postgresql://")))
    engine = "postgres" if (db is not None and not getattr(db, "is_sqlite", True)) else "sqlite"
    return {
        "initialized": (await get_meta(db, "initialized")) == "1",
        "initialized_at": float(await get_meta(db, "initialized_at") or 0),
        "database": {
            "engine": engine,
            "pg_configured": pg_configured,
            "pg_error": app_state.db_error if engine == "sqlite" and pg_configured else "",
            "hint": (
                "postgres در .env تنظیم شده اما در دسترس نیست — سرویس با SQLite ادامه می‌دهد؛ "
                "URL/دسترسی را بررسی کنید یا SQLite را انتخاب کنید"
                if engine == "sqlite" and pg_configured
                else ""
            ),
        },
    }


class SetupCompleteIn(BaseModel):
    new_password: str = ""
    default_backend: str = ""
    storage_chat: str = ""  # telegram storage channel (@username or -100… id)
    db_engine: str = ""  # 'sqlite' | 'postgres' — wizard choice (postgres only honored when reachable)


@router.post("/setup/complete")
async def setup_complete(
    body: SetupCompleteIn,
    admin: str = Depends(get_current_admin),
    db=Depends(get_db),
):
    from ..core.models import UserRepo
    from ..core.settings_service import get_meta, set_meta, set_engine_hint

    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(status_code=400, detail="password too short (min 6)")
        await UserRepo(db).set_password(admin, body.new_password)
    if body.db_engine:
        engine = body.db_engine.strip().lower()
        if engine not in ("sqlite", "postgres"):
            raise HTTPException(status_code=400, detail="db_engine must be 'sqlite' or 'postgres'")
        if engine == "postgres" and db.is_sqlite:
            s = get_settings()
            if not (s.database_url or "").strip().startswith(("postgres://", "postgresql://")):
                raise HTTPException(
                    status_code=400,
                    detail="postgres انتخاب شد اما TGDRIVE_DATABASE_URL در .env تنظیم نشده؛ ابتدا URL را تنظیم و سرویس را ری‌استارت کنید",
                )
            if state.db_error:
                raise HTTPException(
                    status_code=409,
                    detail=f"postgres در دسترس نیست: {state.db_error[:200]} — اتصال را برقرار کنید یا sqlite را انتخاب کنید",
                )
        # persist the wizard's engine choice (honored on next restarts)
        await set_meta(db, "db_engine", engine)
        set_engine_hint(engine)
    if body.default_backend:
        be = body.default_backend.strip().lower()
        if be not in ("telegram", "eitaa"):
            raise HTTPException(status_code=400, detail="backend must be 'telegram' or 'eitaa'")
        from ..core.settings_service import save_runtime_settings

        await save_runtime_settings(db, {"default_backend": be}, actor=admin)
    if body.storage_chat.strip():
        from ..core.settings_service import save_runtime_settings

        await save_runtime_settings(db, {"tg_storage_chat": body.storage_chat.strip()}, actor=admin)
    if (await get_meta(db, "initialized")) != "1":
        await set_meta(db, "initialized", "1")
        await set_meta(db, "initialized_at", str(time.time()))
    await db.audit(admin, "setup.complete")
    return {"ok": True, "initialized": True}


# ── storage channels report ──────────────────────────────────────
MIME_GROUP_LABELS = {
    "image": "تصویر",
    "video": "ویدیو",
    "audio": "صوت",
    "text": "متن/کد",
    "archive": "آرشیو",
    "application": "فایل/برنامه",
    "other": "سایر",
}


def _mime_group(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("text/"):
        return "text"
    if m.startswith("application/"):
        if any(x in m for x in ("zip", "tar", "rar", "7z", "gzip", "compressed", "x-iso")):
            return "archive"
        return "application"
    return "other"


@router.get("/storage-channels")
async def storage_channels(_: str = Depends(get_current_admin), db=Depends(get_db)):
    """Per storage channel: file count, total size and per-type breakdown.

    Keys pin a dedicated channel (api_keys.storage_chat); files fall back to the
    system default channel when a key has none. Both surfaces are reported so
    the panel can show each channel with its owning keys and contents.
    """
    key_rows = await db.fetch_all(
        "SELECT id, name, backend, revoked, storage_chat FROM api_keys ORDER BY id"
    )
    keys_by_chat: Dict[str, List[dict]] = {}
    default_keys: List[dict] = []
    for k in key_rows:
        chat = (k.get("storage_chat") or "").strip()
        entry = {"id": k["id"], "name": k["name"], "backend": k.get("backend") or "",
                 "revoked": bool(k.get("revoked"))}
        if chat:
            keys_by_chat.setdefault(chat, []).append({**entry, "scope": "dedicated"})
        else:
            default_keys.append({**entry, "scope": "default"})

    default_chat = ""
    try:
        from ..core.settings_service import get_runtime

        default_chat = str((await get_runtime(db, "tg_storage_chat")) or "").strip()
    except Exception:
        default_chat = ""

    file_rows = await db.fetch_all(
        "SELECT COALESCE(NULLIF(storage_chat, ''), ?) AS chat, mime,"
        " COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes"
        " FROM files WHERE deleted_at IS NULL AND status='ready'"
        " GROUP BY 1, mime",
        (default_chat,),
    )
    ch: Dict[str, dict] = {}
    for r in file_rows:
        chat = r["chat"] or ""
        e = ch.setdefault(chat, {"files": 0, "bytes": 0, "by_type": {}})
        e["files"] += int(r["n"] or 0)
        e["bytes"] += int(r["bytes"] or 0)
        g = _mime_group(r["mime"])
        t = e["by_type"].setdefault(g, {"key": g, "count": 0, "bytes": 0})
        t["count"] += int(r["n"] or 0)
        t["bytes"] += int(r["bytes"] or 0)

    items = []
    order: List[str] = []
    if default_chat or default_keys:
        order.append(default_chat)
    for chat in keys_by_chat:
        if chat not in order:
            order.append(chat)
    for chat in ch:
        if chat not in order:
            order.append(chat)
    for chat in order:
        s = ch.get(chat, {"files": 0, "bytes": 0, "by_type": {}})
        if chat == default_chat:
            keys = default_keys
            kind = "default"
        else:
            keys = keys_by_chat.get(chat, [])
            kind = "dedicated"
        by_type = [s["by_type"][g] for g in sorted(s["by_type"], key=lambda g: -s["by_type"][g]["count"])]
        for t in by_type:
            t["label"] = MIME_GROUP_LABELS.get(t["key"], t["key"])
        items.append({
            "chat": chat,
            "kind": kind,
            "is_system_default": chat == default_chat and bool(chat),
            "keys": keys,
            "files": s["files"],
            "bytes": s["bytes"],
            "by_type": by_type,
        })
    return {"items": items, "default_chat": default_chat}
@router.get("/nodes")
async def nodes(_: str = Depends(get_current_admin), db=Depends(get_db)):
    return {
        "items": await db.fetch_all("SELECT * FROM nodes ORDER BY last_heartbeat DESC"),
        "node_id": state.node_id,
    }


# ── telegram proxy pool ──────────────────────────────────────────
@router.get("/proxies")
async def proxies_list(_: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import ProxyRepo

    return {"items": await ProxyRepo(db).list()}


class ProxyIn(BaseModel):
    link: str = ""  # tg://proxy / t.me/proxy / socks5://user:pass@host:port / host:port[:user:pass]
    host: str = ""
    port: int = 0
    kind: str = "socks5"  # mtproto | socks5 | http
    label: str = ""
    username: str = ""
    password: str = ""
    secret_hex: str = ""


@router.post("/proxies")
async def proxies_add(body: ProxyIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import ProxyRepo
    from ..services.proxy_service import parse_share_link

    try:
        if body.link:
            parsed = parse_share_link(body.link)
        else:
            if not body.host or not (0 < int(body.port) < 65536):
                raise ValueError("host/port نامعتبر است")
            parsed = {
                "kind": body.kind, "host": body.host, "port": int(body.port),
                "secret_hex": body.secret_hex, "username": body.username, "password": body.password,
            }
        proxy_id = await ProxyRepo(db).create(
            host=parsed["host"], port=parsed["port"], kind=parsed.get("kind", "socks5"),
            label=body.label or parsed.get("host", ""),
            username=parsed.get("username", ""),
            password=parsed.get("password", ""),
            secret_hex=parsed.get("secret_hex", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from ..services import proxy_service

    proxy_service.selector.invalidate()
    await db.audit(admin, "proxy.add", target=str(proxy_id), details=f"{parsed.get('kind')}://{parsed['host']}:{parsed['port']}")
    return {"ok": True, "id": proxy_id}


@router.delete("/proxies/{proxy_id}")
async def proxies_delete(proxy_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import ProxyRepo

    n = await ProxyRepo(db).delete(proxy_id)
    if not n:
        raise HTTPException(status_code=404, detail="proxy not found")
    from ..services import proxy_service

    proxy_service.selector.invalidate()
    await db.audit(admin, "proxy.delete", target=str(proxy_id))
    return {"ok": True}


class ProxyPatch(BaseModel):
    enabled: bool


@router.patch("/proxies/{proxy_id}")
async def proxies_patch(proxy_id: int, body: ProxyPatch, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import ProxyRepo
    from ..services import proxy_service

    n = await ProxyRepo(db).set_enabled(proxy_id, body.enabled)
    if not n:
        raise HTTPException(status_code=404, detail="proxy not found")
    proxy_service.selector.invalidate()
    await db.audit(admin, "proxy.toggle", target=str(proxy_id), details=str(body.enabled))
    return {"ok": True}


@router.post("/proxies/test")
async def proxies_test_all(admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Speed-test all proxies concurrently; returns rows sorted by latency."""
    from ..core.models import ProxyRepo
    from ..services import proxy_service

    items = await proxy_service.speed_test_all(ProxyRepo(db))
    await db.audit(admin, "proxy.test_all", details=f"{len(items)} proxies")
    return {"items": items}


@router.post("/proxies/{proxy_id}/test")
async def proxies_test_one(proxy_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    from ..core.models import ProxyRepo
    from ..services import proxy_service

    rows = await proxy_service.speed_test_all(ProxyRepo(db), proxy_id=proxy_id)
    if not rows:
        raise HTTPException(status_code=404, detail="proxy not found")
    await db.audit(admin, "proxy.test", target=str(proxy_id))
    return {"item": rows[0]}


@router.post("/proxies/apply")
async def proxies_apply(admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Reconnect telegram backends with the current proxy decision."""
    from ..services import proxy_service

    proxy_service.selector.invalidate()
    if state.manager is not None and hasattr(state.manager, "reload_all"):
        await state.manager.reload_all()
    await db.audit(admin, "proxy.apply")
    return {"ok": True}
