"""The Telegram webhook must only accept requests from Telegram.

Telegram echoes the secret token we registered with `set_webhook` in
the `X-Telegram-Bot-Api-Secret-Token` header. Without checking it,
anyone who guesses the URL could drive the bot as a user.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


SECRET = "s3cr3t-telegram-token"


@pytest.fixture
def telegram_secret(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "telegram_secret_token", SECRET)
    return SECRET


def test_webhook_rejects_request_without_secret_token(
    client: TestClient, telegram_secret
) -> None:
    resp = client.post("/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 403


def test_webhook_rejects_request_with_wrong_secret_token(
    client: TestClient, telegram_secret
) -> None:
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 403


def test_webhook_accepts_request_with_correct_secret_token(
    client: TestClient, telegram_secret
) -> None:
    """With the right token the request passes authentication.

    The bot is not running in the test environment, so the route
    answers 503 ("Telegram bot is not running") - which is exactly
    what proves the secret-token check was passed.
    """
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 503
    assert "not running" in resp.json()["detail"].lower()
