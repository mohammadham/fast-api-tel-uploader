"""Structured JSON logging + request/correlation-id tests."""
from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest


async def test_request_id_echoed_and_new(client):
    r = await client.get("/api/v1/admin/healthz")
    assert r.status_code == 200
    rid = r.headers["x-request-id"]
    corr = r.headers["x-correlation-id"]
    assert rid.startswith("req_")
    assert corr == rid  # without upstream headers they are the same


async def test_upstream_ids_are_honored(client):
    r = await client.get(
        "/api/v1/admin/healthz",
        headers={"X-Request-ID": "my-req-123", "X-Correlation-ID": "chain-abc"},
    )
    assert r.headers["x-request-id"] == "my-req-123"
    assert r.headers["x-correlation-id"] == "chain-abc"


async def test_access_log_is_valid_json_with_ids(client, caplog):
    with caplog.at_level(logging.INFO, logger="tgdrive.access"):
        await client.get("/api/v1/admin/healthz")
    recs = [r for r in caplog.records if r.name == "tgdrive.access"]
    assert recs, "no access log emitted"
    rec = recs[-1]
    line = rec.getMessage()  # our slog stores msg via record; JSON is in getMessage? no —
    # slog uses logger.log(msg, extra=fields); the JSON rendering happens in the
    # formatter. caplog gives us the record, so rebuild what JsonFormatter makes.
    from app.core.obs import JsonFormatter

    rendered = json.loads(JsonFormatter().format(rec))
    assert rendered["msg"] == "request"
    assert rendered["path"] == "/api/v1/admin/healthz"
    assert rendered["status"] == 200
    assert rendered["request_id"].startswith("req_")
    assert rendered["logger"] == "tgdrive.access"


async def test_job_correlation_id_flows_to_queue(client, api_key):
    """enqueue inside a request must persist the request's correlation id."""
    from app.core.obs import correlation_id as corr_var, request_id as rid_var

    # simulate the middleware context (ASGITransport runs our middleware, so
    # inside the endpoint the contextvar is already set — verify via job row)
    H = {"Authorization": f"Bearer {api_key}", "X-Correlation-ID": "corr-e2e-42"}
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("corr.bin", io.BytesIO(b"c" * 128), "application/octet-stream")},
    )
    assert r.status_code == 200
    fid = r.json()["file_id"]

    # wait for the upload job to be picked/done, then inspect jobs table
    from app.core.state import state

    for _ in range(200):
        if (await client.get(f"/api/v1/files/{fid}", headers=H)).json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)
    rows = await state.db.fetch_all("SELECT correlation_id FROM jobs ORDER BY created_at DESC LIMIT 1")
    assert rows and rows[0]["correlation_id"] == "corr-e2e-42"


async def test_job_done_log_contains_ids(client, api_key, caplog):
    H = {"Authorization": f"Bearer {api_key}"}
    r = await client.post(
        "/api/v1/files/upload",
        headers=H,
        files={"file": ("log.bin", io.BytesIO(b"l" * 64), "application/octet-stream")},
    )
    fid = r.json()["file_id"]
    with caplog.at_level(logging.INFO, logger="tgdrive.queue"):
        for _ in range(200):
            if (await client.get(f"/api/v1/files/{fid}", headers=H)).json()["status"] == "ready":
                break
            await asyncio.sleep(0.05)
    from app.core.obs import JsonFormatter

    recs = [r for r in caplog.records if r.name == "tgdrive.queue" and getattr(r, "msg_id", None) is None]
    done = [r for r in caplog.records if "job done" in r.getMessage()]
    assert done, "no 'job done' structured log"
    rendered = json.loads(JsonFormatter().format(done[-1]))
    assert rendered["kind"] == "upload"
    assert "duration_ms" in rendered
    # correlation propagated from the HTTP request into worker context
    assert rendered.get("correlation_id"), rendered


async def test_json_formatter_flat_and_valid():
    from app.core.obs import JsonFormatter, slog

    logger = logging.getLogger("tgdrive.test.json")
    logger.setLevel(logging.DEBUG)
    records = []
    handler = logging.Handler()
    handler.emit = lambda rec: records.append(rec)
    logger.addHandler(handler)

    slog_l = slog("tgdrive.test.json")
    slog_l.info("hello", file_id="f_1", size=10, nested={"a": 1})

    rendered = json.loads(JsonFormatter().format(records[0]))
    assert rendered["msg"] == "hello"
    assert rendered["file_id"] == "f_1"
    assert rendered["size"] == 10
    assert rendered["nested"] == {"a": 1}


async def test_exception_includes_exc_field(client):
    # hit a 500 path indirectly? we don't have a deliberate crash endpoint;
    # instead verify the formatter path directly
    from app.core.obs import JsonFormatter

    try:
        raise ValueError("boom-ctx")
    except ValueError:
        import sys

        rec = logging.LogRecord(
            name="t", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="crashed", args=(), exc_info=sys.exc_info(),
        )
        rendered = json.loads(JsonFormatter().format(rec))
    assert "exc" in rendered and "boom-ctx" in rendered["exc"]
