"""Health-report endpoint: server + sessions + database in one payload."""
from __future__ import annotations


async def test_health_report_shape(client, token):
    """/admin/health-report aggregates server/sessions/database for the dashboard."""
    r = await client.get("/api/v1/admin/health-report", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    d = r.json()

    # server: running inside the app → in_app with live queue/uptime
    assert d["server"]["in_app"] is True
    assert d["server"]["ok"] is True
    assert d["server"]["uptime_s"] >= 0
    assert d["server"]["fake_tg"] is True  # test env
    assert set(d["server"]["queue"]) == {"pending", "running", "retry", "failed", "done"}

    # sessions: the conftest fake account decrypts fine
    assert d["sessions"]["accounts_ok"] == 1
    assert d["sessions"]["accounts"][0]["session_ok"] is True
    assert d["sessions"]["all_ok"] is True

    # database: sqlite with integrity check
    assert d["database"]["engine"] == "sqlite"
    assert d["database"]["integrity"] == "ok"
    assert d["database"]["files"]["total"] >= 0


async def test_health_report_requires_admin(client):
    r = await client.get("/api/v1/admin/health-report")
    assert r.status_code == 401
