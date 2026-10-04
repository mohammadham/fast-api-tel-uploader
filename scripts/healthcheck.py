"""Standalone health check: server + sessions + database, one report.

Works in two layers, so it is useful even when the API is down:

1. SERVER  — pings /healthz + /readyz (no auth) and, with admin credentials
             (`--username/--password`, defaults read from the data dir's
             initial_admin_password.txt when present), pulls the full report
             from GET /api/v1/admin/health-report.
2. LOCAL   — opens the database directly via the app's own modules and runs
             the exact same build_health_report() the endpoint uses, so the
             numbers always agree.

Exit code: 0 = all critical checks green, 1 = something is wrong (CI/cron
friendly). Use --json for machine-readable output.

Usage:
  .venv/Scripts/python.exe scripts/healthcheck.py
  .venv/Scripts/python.exe scripts/healthcheck.py --base http://127.0.0.1:8765 --no-server
  .venv/Scripts/python.exe scripts/healthcheck.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import get_settings  # noqa: E402


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_uptime(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def _mark(ok: bool) -> str:
    return "OK " if ok else "FAIL"


async def local_deep_check(data_dir: str, with_integrity: bool = True) -> dict:
    """Open the DB directly (works with the server stopped) and build the report."""
    from app.core.db import Database
    from app.services.health_report import build_health_report

    s = get_settings()
    db_path = os.path.join(data_dir, "telegramdrive.db") if data_dir else s.db_path
    db = Database(db_path)
    await db.connect()
    try:
        return await build_health_report(db, with_integrity=with_integrity)
    finally:
        await db.close()


async def server_ping(base: str) -> dict:
    import httpx

    out: dict = {}
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        for name, path in (("healthz", "/api/v1/admin/healthz"), ("readyz", "/api/v1/admin/readyz")):
            try:
                r = await c.get(path)
                out[name] = (r.status_code == 200 and r.json().get("ok") is True)
            except Exception:
                out[name] = False
    return out


async def server_full_report(base: str, username: str, password: str) -> dict | None:
    import httpx

    async with httpx.AsyncClient(base_url=base, timeout=15) as c:
        r = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
        if r.status_code != 200:
            return None
        r = await c.get("/api/v1/admin/health-report",
                        headers={"Authorization": "Bearer " + r.json()["access_token"]})
        return r.json() if r.status_code == 200 else None


def print_report(server_ping_res: dict | None, report: dict) -> bool:
    """Pretty-print; returns True when every critical check is green."""
    crit = True
    print("=" * 62)
    print("TelegramDrive HEALTH CHECK — " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 62)

    print("\n── SERVER ─────────────────────────────────────────────")
    if server_ping_res is None:
        print("  API            : not reachable (server down or --no-server)")
    else:
        hz, rz = server_ping_res.get("healthz"), server_ping_res.get("readyz")
        print(f"  healthz        : [{_mark(hz)}]")
        print(f"  readyz         : [{_mark(rz)}]")
        crit &= bool(hz and rz)
    srv = report.get("server", {})
    if srv.get("in_app"):
        # authoritative live-app numbers (only present via the API report)
        print(f"  app state      : [{_mark(srv.get('ok'))}] uptime {_fmt_uptime(srv.get('uptime_s', 0))}")
        print(f"  mode           : {'FAKE-TG (simulated telegram)' if srv.get('fake_tg') else 'REAL telegram'}")
        print(f"  workers        : download={srv.get('workers', {}).get('download')} upload={srv.get('workers', {}).get('upload')}")
        print(f"  storage chat   : {srv.get('storage_chat') or '(unset)'}")
        q = srv.get("queue", {})
        print(f"  queue          : pending={q.get('pending')} running={q.get('running')} retry={q.get('retry')} failed={q.get('failed')} done={q.get('done')}")
        if q.get("failed"):
            crit = False
    else:
        # standalone DB open: the effective mode is still real (read from the settings table)
        print(f"  mode (from DB) : {'FAKE-TG (simulated telegram)' if srv.get('fake_tg') else 'REAL telegram'}")

    print("\n── SESSIONS ───────────────────────────────────────────")
    ses = report.get("sessions", {})
    for a in ses.get("accounts", []):
        line = (f"  acc:{a['id']:<3} {a['phone']:<16} status={a['status']:<8} "
                f"enabled={int(a['enabled'])} session_decrypt=[{_mark(a['session_ok'])}]")
        print(line + (f"  last_error={a['last_error'][:60]}" if a["last_error"] else ""))
        crit &= a["session_ok"] and a["status"] == "ready" and a["enabled"]
    for b in ses.get("bots", []):
        line = (f"  bot:{b['id']:<3} {b['label']:<16} status={b['status']:<8} "
                f"enabled={int(b['enabled'])} token_decrypt=[{_mark(b['token_ok'])}]")
        print(line + (f"  last_error={b['last_error'][:60]}" if b["last_error"] else ""))
        crit &= b["token_ok"]
    print(f"  summary        : accounts_ok={ses.get('accounts_ok')}/{len(ses.get('accounts', []))} "
          f"bots_ok={ses.get('bots_ok')}/{len(ses.get('bots', []))} all_decrypt_ok={ses.get('all_ok')}")

    print("\n── DATABASE ───────────────────────────────────────────")
    d = report.get("database", {})
    print(f"  engine         : {d.get('engine')}" + (f"  ({d.get('path')})" if d.get("path") else ""))
    if d.get("size_bytes"):
        print(f"  file size      : {_fmt_bytes(d['size_bytes'])}")
    integ = d.get("integrity", "unknown")
    print(f"  integrity      : [{_mark(integ == 'ok')}] {integ}")
    crit &= integ in ("ok", "unknown")  # unknown only on non-sqlite engines
    f = d.get("files", {})
    print(f"  files          : {f.get('ready', 0)} ready / {f.get('total', 0)} total · {_fmt_bytes(f.get('bytes', 0))} · trash={f.get('trashed', 0)}")
    print(f"  folders/keys   : {d.get('folders')} folders · {d.get('api_keys_active')} active api keys · {d.get('channels')} channels")
    print(f"  failed jobs    : {d.get('jobs_failed', 0)}")

    print("\n" + ("RESULT: ALL GREEN" if crit else "RESULT: PROBLEMS FOUND (see " + "\N{BALLOT X}" + " above)"))
    return crit


async def main() -> int:
    ap = argparse.ArgumentParser(description="TelegramDrive health check")
    ap.add_argument("--base", default=f"http://127.0.0.1:{os.environ.get('TGDRIVE_PORT', '8765')}")
    ap.add_argument("--data-dir", default="",
                    help="data dir holding telegramdrive.db (default: auto-detect env/data-livepanel/data-live/data)")
    ap.add_argument("--username", default=os.environ.get("TGDRIVE_ADMIN_USERNAME", "admin"))
    ap.add_argument("--password", default="")
    ap.add_argument("--no-server", action="store_true", help="skip API ping, local DB check only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-integrity", action="store_true", help="skip sqlite quick_check (big DBs)")
    args = ap.parse_args()

    # auto-detect the data dir when not given explicitly
    data_dir = args.data_dir or os.environ.get("TGDRIVE_DATA_DIR", "")
    if not data_dir:
        for cand in ("data-livepanel", "data-live", "data"):
            if os.path.exists(os.path.join(cand, "telegramdrive.db")):
                data_dir = cand
                break

    ping = None
    api_report = None
    if not args.no_server:
        ping = await server_ping(args.base)
        pwd = args.password
        if not pwd:
            for cand in (os.path.join(data_dir, "initial_admin_password.txt") if data_dir else "",
                         "data-livepanel/initial_admin_password.txt", "data/initial_admin_password.txt"):
                if cand and os.path.exists(cand):
                    pwd = open(cand, encoding="utf-8").read().strip()
                    break
        if pwd:
            api_report = await server_full_report(args.base, args.username, pwd)

    local = await local_deep_check(data_dir, with_integrity=not args.no_integrity)
    report = api_report or local

    if args.json:
        print(json.dumps({"server_ping": ping, "report": report}, ensure_ascii=False, indent=2))
        ok = bool(ping and all(ping.values())) if ping else True
        return 0 if ok and report.get("sessions", {}).get("all_ok") else 1

    ok = print_report(ping, report)
    if ping is not None and api_report is None:
        print("\n(note: full API report unavailable — server too old (no /health-report) or login failed; showing local DB check)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
