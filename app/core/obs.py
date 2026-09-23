"""Structured (JSON) logging + request/correlation id propagation.

Every log line is a single JSON object so production log shippers (Loki,
ELK, CloudWatch...) can index fields directly. A ``ContextFilter`` stamps
request_id / correlation_id / job_id from contextvars onto every record:

- request_id:      unique per HTTP request (or echoed from X-Request-ID)
- correlation_id:  stable across an upstream chain (X-Correlation-ID) and
                   propagated into queue jobs so worker logs link back to
                   the original API call
- job_id:          set while a queue worker processes a job

IDs also come back to clients via X-Request-ID / X-Correlation-ID response
headers for support/debug round-trips.
"""
from __future__ import annotations

import contextvars
import json
import logging
import secrets
import sys
import time
from typing import Any

# ── context variables ─────────────────────────────────────────
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
job_id: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="")


def new_request_id() -> str:
    return "req_" + secrets.token_hex(8)


def current_ids() -> dict[str, str]:
    return {
        "request_id": request_id.get(""),
        "correlation_id": correlation_id.get(""),
        "job_id": job_id.get(""),
    }


# ── stamping filter: contextvars → record attributes ─────────
class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get("")  # type: ignore[attr-defined]
        record.correlation_id = correlation_id.get("")  # type: ignore[attr-defined]
        record.job_id = job_id.get("")  # type: ignore[attr-defined]
        return True


# ── JSON formatter ────────────────────────────────────────────
_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "stack_print", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra kwargs pass through as top-level fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "correlation_id", "job_id"):
            val = getattr(record, key, "")
            if val:
                payload[key] = val
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_") or key in payload or key in ("request_id", "correlation_id", "job_id"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if any(getattr(h, "_tgdrive_json", False) for h in root.handlers):
        return
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._tgdrive_json = True  # type: ignore[attr-defined]
    handler.addFilter(ContextFilter())
    root.addHandler(handler)


# ── convenience: structured logger ────────────────────────────
class StructuredLogger:
    """logger.info("msg", **fields) sugar — fields become JSON keys."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, msg: str, exc_info: bool = False, **fields: Any) -> None:
        self._logger.log(level, msg, exc_info=exc_info, extra=fields)

    def debug(self, msg: str, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, **fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._log(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._log(logging.WARNING, msg, **fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, exc_info=True, **fields)


def slog(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))


def monotonic_ms() -> float:
    return time.perf_counter() * 1000
