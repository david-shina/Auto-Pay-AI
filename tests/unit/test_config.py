"""Unit tests for the settings module.

The most important property here is that the application ships no
credentials: every secret must come from the environment, and the
process must refuse to start in production without them.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings, _validate_startup


def _minimal_env() -> dict:
    return {
        "jwt_secret_key": "a" * 40,
        "bvn_encryption_key": Fernet.generate_key().decode(),
        "paystack_secret_key": "sk_test_whatever",
    }


def test_settings_ship_no_secrets() -> None:
    """No default in the config module may look like a credential."""
    defaults = Settings.model_fields
    for name in (
        "jwt_secret_key",
        "bvn_encryption_key",
        "paystack_secret_key",
        "paystack_public_key",
        "telegram_bot_token",
        "telegram_secret_token",
        "groq_api_key",
        "langchain_api_key",
    ):
        assert defaults[name].default in ("", None), (
            f"{name} must not ship a default value, got "
            f"{defaults[name].default!r}"
        )


def test_settings_have_no_embedded_password() -> None:
    """The default DATABASE_URL must not carry a project credential."""
    url = Settings().database_url
    assert "change-me" not in url
    assert url.startswith("postgresql://")


def test_production_refuses_to_start_without_secrets() -> None:
    # Explicit empty values: the test environment has these set in the
    # process environment, and init arguments win over env vars.
    s = Settings(
        environment="production",
        jwt_secret_key="",
        bvn_encryption_key="",
        paystack_secret_key="",
    )
    with pytest.raises(RuntimeError) as exc:
        _validate_startup(s)
    message = str(exc.value)
    assert "JWT_SECRET_KEY" in message
    assert "BVN_ENCRYPTION_KEY" in message
    assert "PAYSTACK_SECRET_KEY" in message


def test_production_requires_telegram_secret_token_in_webhook_mode() -> None:
    s = Settings(
        environment="production",
        webhook_url="https://example.com/telegram/webhook",
        **_minimal_env(),
    )
    with pytest.raises(RuntimeError) as exc:
        _validate_startup(s)
    assert "TELEGRAM_SECRET_TOKEN" in str(exc.value)


def test_production_accepts_complete_configuration() -> None:
    s = Settings(
        environment="production",
        telegram_secret_token="s3cret",
        webhook_url="https://example.com/telegram/webhook",
        bcrypt_rounds=12,
        **_minimal_env(),
    )
    assert _validate_startup(s) is s


def test_development_generates_an_ephemeral_jwt_key() -> None:
    """Outside production the app still starts, with a loud warning."""
    s = Settings(environment="development")
    result = _validate_startup(s)
    assert result.jwt_secret_key
    # The generated key is not the shipped default.
    assert result.jwt_secret_key != Settings.model_fields["jwt_secret_key"].default


def test_low_bcrypt_rounds_are_rejected_outside_tests() -> None:
    s = Settings(
        environment="production",
        bcrypt_rounds=4,
        **_minimal_env(),
    )
    with pytest.raises(RuntimeError) as exc:
        _validate_startup(s)
    assert "BCRYPT_ROUNDS" in str(exc.value)


def test_low_bcrypt_rounds_are_allowed_in_tests() -> None:
    s = Settings(environment="test", bcrypt_rounds=4, **_minimal_env())
    assert _validate_startup(s) is s
