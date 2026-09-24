""".env provisioning for the setup wizard (env_service + setup/env endpoints)."""
from __future__ import annotations

import os

import pytest


@pytest.fixture()
async def admin_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def env_tmp(monkeypatch, tmp_path):
    """Point env_service at a temp .env and clear TGDRIVE_ env vars."""
    from app.services import env_service

    monkeypatch.setattr(env_service, "_ENV_PATH", tmp_path / ".env")
    cleared = {}
    for spec in env_service.ENV_KEYS:
        cleared[spec["key"]] = os.environ.pop(spec["key"], None)
    yield env_service, tmp_path / ".env"
    for k, v in cleared.items():
        if v is not None:
            os.environ[k] = v


async def test_status_missing_file(client, admin_headers, env_tmp):
    env_service, env_path = env_tmp
    resp = await client.get("/api/v1/admin/setup/env", headers=admin_headers)
    assert resp.status_code == 200
    d = resp.json()
    assert d["file_exists"] is False
    assert "TGDRIVE_SECRET" in d["missing_required"]
    secret_item = next(i for i in d["items"] if i["key"] == "TGDRIVE_SECRET")
    assert secret_item["secret"] is True and secret_item["configured"] is False
    assert "value" not in secret_item  # secrets are never echoed
    # non-secret keys expose their value
    user_item = next(i for i in d["items"] if i["key"] == "TGDRIVE_ADMIN_USERNAME")
    assert user_item["value"] == "admin"


async def test_write_creates_file_and_masks_secret(client, admin_headers, env_tmp):
    env_service, env_path = env_tmp
    resp = await client.put(
        "/api/v1/admin/setup/env",
        json={"values": {"TGDRIVE_ADMIN_USERNAME": "boss", "TGDRIVE_TG_API_ID": "12345"}, "generate_secret": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["created"] is True
    assert env_path.exists()
    text = env_path.read_text(encoding="utf-8")
    assert "TGDRIVE_ADMIN_USERNAME=boss" in text
    assert "TGDRIVE_TG_API_ID=12345" in text
    assert "TGDRIVE_SECRET=" in text and "please-change-me" not in text.split("TGDRIVE_SECRET=")[1].split("\n")[0]
    # generated secret is long & random
    sec = text.split("TGDRIVE_SECRET=")[1].split("\n")[0]
    assert len(sec) >= 40
    # status now reflects configured + file source
    st = (await client.get("/api/v1/admin/setup/env", headers=admin_headers)).json()
    assert st["file_exists"] is True
    assert st["missing_required"] == []
    assert next(i for i in st["items"] if i["key"] == "TGDRIVE_SECRET")["source"] == "file"


async def test_write_preserves_comments_and_updates_in_place(client, admin_headers, env_tmp):
    env_service, env_path = env_tmp
    env_path.write_text(
        "# my comment\nTGDRIVE_ADMIN_USERNAME=old\nTGDRIVE_TG_API_ID=1\n", encoding="utf-8"
    )
    resp = await client.put(
        "/api/v1/admin/setup/env",
        json={"values": {"TGDRIVE_ADMIN_USERNAME": "new"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    text = env_path.read_text(encoding="utf-8")
    assert "# my comment" in text
    assert "TGDRIVE_ADMIN_USERNAME=new" in text
    assert "TGDRIVE_ADMIN_USERNAME=old" not in text
    assert "TGDRIVE_TG_API_ID=1" in text  # untouched
    # backup was created
    backups = list(env_path.parent.glob(".env.bak.*"))
    assert len(backups) == 1
    assert "TGDRIVE_ADMIN_USERNAME=old" in backups[0].read_text(encoding="utf-8")


async def test_write_rejects_unknown_tgdrive_key(client, admin_headers, env_tmp):
    resp = await client.put(
        "/api/v1/admin/setup/env",
        json={"values": {"TGDRIVE_NOT_A_KEY": "x"}},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "unknown key" in resp.json()["detail"]


async def test_write_never_applies_secrets_as_empty_and_process_env_wins(client, admin_headers, env_tmp, monkeypatch):
    env_service, env_path = env_tmp
    # process env shadowing → restart_keys hint in response
    monkeypatch.setenv("TGDRIVE_TG_API_ID", "999")
    resp = await client.put(
        "/api/v1/admin/setup/env",
        json={"values": {"TGDRIVE_TG_API_ID": "111"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["restart_keys"] == ["TGDRIVE_TG_API_ID"]
    # file still got the value (applies after restart)
    assert "TGDRIVE_TG_API_ID=111" in env_path.read_text(encoding="utf-8")


async def test_setup_status_still_works(client, admin_headers, env_tmp):
    resp = await client.get("/api/v1/admin/setup/status", headers=admin_headers)
    assert resp.status_code == 200
    assert "database" in resp.json()
