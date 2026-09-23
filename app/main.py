"""TelegramDrive — FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from .api import accounts, admin, auth, bots, eitaa, files, keys, queue as queue_api
from .api.deps import get_current_admin as _admin_dep
from .core.config import get_settings
from .core.db import Database
from .core.models import UserRepo
from .core.obs import current_ids, monotonic_ms, new_request_id, request_id as rid_var, correlation_id as corr_var, setup_logging, slog
from .core.rate_limit import limiter
from .core.security import create_token, decode_token, hash_password
from .core.state import state
from .queue.queue_manager import QueueManager
from .services.janitor import Janitor
from .tg.bot_service import BotService
from .tg.manager import TGManager

log = logging.getLogger("tgdrive.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    os.makedirs(s.data_dir, exist_ok=True)
    os.makedirs(s.final_tmp_dir(), exist_ok=True)

    db = Database(s.final_db_path())
    await db.connect()
    state.db = db

    # bootstrap admin
    username = s.admin_username or "admin"
    password = s.admin_password
    if not password:
        pwd_file = os.path.join(s.data_dir, "initial_admin_password.txt")
        if os.path.exists(pwd_file):
            password = open(pwd_file).read().strip()
        else:
            password = secrets.token_urlsafe(16)
            with open(pwd_file, "w") as fh:
                fh.write(password)
            try:
                os.chmod(pwd_file, 0o600)
            except OSError:
                pass
            log.warning("generated admin password stored at %s", pwd_file)
    await UserRepo(db).ensure_admin(username, password)

    # services
    manager = TGManager(db)
    await manager.start()
    state.manager = manager

    queue = QueueManager(db, manager)
    state.queue = queue

    bots = BotService(db, manager, queue)
    state.bots = bots

    await queue.start()
    await bots.start_all()

    janitor = Janitor(db)
    janitor.start()
    state.janitor = janitor

    yield

    await janitor.stop()
    await bots.stop_all()
    await queue.stop()
    await manager.stop()
    await db.close()


app = FastAPI(title="TelegramDrive", version="1.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def _audit_http_exc(request: StarletteRequest, exc: HTTPException):
    """Audit failed auth attempts (login/refresh) — basic security telemetry."""
    path = request.url.path
    # note: /login audits its own failures (with username) inside the endpoint;
    # this covers refresh + any other auth-path 401
    if (
        path.startswith("/api/v1/auth/")
        and path != "/api/v1/auth/login"
        and exc.status_code == 401
        and request.method == "POST"
    ):
        try:
            from .core.state import state

            if state.db is not None:
                await state.db.audit(
                    "anonymous", "auth.login.fail", target=path,
                    ip=(request.client.host if request.client else ""),
                )
        except Exception:
            pass
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Request-ID/Correlation-ID stamping + structured access log.

    - request_id: per-request UUID-ish id (echoed from X-Request-ID if given)
    - correlation_id: stable across an upstream chain (X-Correlation-ID), else new
    Both are set as contextvars (visible in every log line) and echoed back
    as response headers so clients can report them.
    """

    # paths too noisy / too hot for access logs (large binary streams)
    SKIP_LOG_PATHS = {"/d/", "/api/v1/files/"}
    SKIP_LOG_SUFFIXES = (".js", ".css", ".map", ".ico", ".png", ".svg")

    async def dispatch(self, request, call_next):
        req_id = request.headers.get("x-request-id", "").strip() or new_request_id()
        corr_id = request.headers.get("x-correlation-id", "").strip() or req_id
        token_r = rid_var.set(req_id)
        token_c = corr_var.set(corr_id)
        t0 = monotonic_ms()
        try:
            response = await call_next(request)
        except Exception:
            dur = round(monotonic_ms() - t0, 1)
            slog("tgdrive.access").error(
                "request crashed",
                method=request.method,
                path=request.url.path,
                duration_ms=dur,
                **current_ids(),
            )
            rid_var.reset(token_r)
            corr_var.reset(token_c)
            raise
        dur = round(monotonic_ms() - t0, 1)
        response.headers.setdefault("X-Request-ID", req_id)
        response.headers.setdefault("X-Correlation-ID", corr_id)
        path = request.url.path
        noisy = (
            any(path.startswith(p) for p in self.SKIP_LOG_PATHS)
            or path.endswith(self.SKIP_LOG_SUFFIXES)
        )
        if not noisy:
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            slog("tgdrive.access")._log(
                level,
                "request",
                method=request.method,
                path=path,
                status=response.status_code,
                duration_ms=dur,
                ip=(request.client.host if request.client else ""),
                **current_ids(),
            )
        rid_var.reset(token_r)
        corr_var.reset(token_c)
        return response


app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

s = get_settings()
origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
if origins != ["*"]:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
else:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(bots.router)
app.include_router(eitaa.router)
app.include_router(keys.router)
app.include_router(files.router)
app.include_router(files.public)
app.include_router(queue_api.router)
app.include_router(admin.router)


@app.get("/")
async def index():
    """Landing page: shows login form if no token, otherwise app view."""
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"service": "TelegramDrive", "docs": "/docs"}


if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/{path:path}", include_in_schema=False)
async def not_found(path: str):
    """Catch-all 404 handler - only matches if no other route matches."""
    raise HTTPException(status_code=404, detail=f'Route {path} not found')


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("TGDRIVE_PORT", "8000")), lifespan="on")


if __name__ == "__main__":
    main()
