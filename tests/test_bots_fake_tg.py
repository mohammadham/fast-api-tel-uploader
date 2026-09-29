"""Bots API — adding a bot must work in fake-TG mode without any real Bot API call.

Regression: add_bot always did a real getMe against api.telegram.org, so in
fake-TG dev/test setups (and any offline panel) creating a bot from the UI
always failed with "token rejected by telegram (getMe failed)".
"""
from __future__ import annotations


async def test_add_bot_fake_tg_creates_local_bot(client, token):
    resp = await client.post(
        "/api/v1/bots",
        json={"token": "123456:AAHfakeTokenForUiReview1234567890", "label": "ui-bot"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["username"] == "fake_bot"

    listing = await client.get("/api/v1/bots", headers={"Authorization": f"Bearer {token}"})
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["label"] == "ui-bot"
    assert items[0]["status"] == "ready"


async def test_add_bot_fake_tg_rejects_malformed_token(client, token):
    resp = await client.post(
        "/api/v1/bots",
        json={"token": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "invalid bot token format" in resp.json()["detail"]
