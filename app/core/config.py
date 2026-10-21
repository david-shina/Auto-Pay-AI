"""Centralised application configuration.

All env-var access goes through the `Settings` object so values are
validated at startup and easy to mock in tests.

Security note: this module deliberately ships **no** secret values.
Every credential comes from the environment (or `.env`) and must be
supplied by the operator. `get_settings()` enforces that in the
`production` environment: the process refuses to start when a required
secret is missing, instead of silently falling back to a default.
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.core.config")


class Settings(BaseSettings):
    """Application settings (loaded from .env / environment)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_port: int = 8000
    app_name: str = "auto-pay-ai"
    # When False the scheduler still starts, but its jobs return
    # immediately. Used by the test suite so a background job can never
    # pay a bill while another test is asserting on wallet balances.
    scheduler_enabled: bool = True

    # ── Database ──
    # Resolution order (highest priority first):
    #   1. DATABASE_URL environment variable
    #   2. DATABASE_URL in your .env file
    #   3. This default - a local PostgreSQL on the standard port. The
    #      password here is the conventional local "postgres" password,
    #      not a credential of this project; override it in .env.
    database_url: str = "postgresql://postgres:postgres@localhost:5432/autopay"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # ── Paystack ──
    payment_provider: Literal["paystack"] = "paystack"
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_base_url: str = "https://api.paystack.co"

    # When True, signup synchronously calls the payment provider to
    # create a DVA for the user. When False (default), signup just
    # creates the user; the user (or you) calls POST /api/v1/wallet/provision
    # later to provision a DVA. Default off because the Paystack
    # Dedicated NUBAN feature requires business approval; turning it
    # on prematurely causes signup to silently fail-audit the DVA step.
    auto_provision_dva_on_signup: bool = False

    # ── Telegram ──
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # e.g. "AutoPayAiBot" - used for deep links
    # Secret Telegram echoes back in the `X-Telegram-Bot-Api-Secret-Token`
    # header. We compare it on every inbound webhook request, so only
    # Telegram can drive the bot. Required when webhook mode is on.
    telegram_secret_token: str = ""
    # Empty means long polling (local development). Set to
    # "https://<host>/telegram/webhook" to run in webhook mode.
    webhook_url: str = ""

    # ── CORS ──
    # Comma-separated list of origins allowed to call the API from a
    # browser. Default covers the common local-dev setups: Vite (5173),
    # Create-React-App (3000), and the FastAPI server itself (8000).
    # Override with the `ALLOWED_ORIGINS` env var (comma-separated)
    # for production / preview deploys.
    allowed_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://localhost:8000,"
        "http://127.0.0.1:8000"
    )

    # ── LLM ──
    groq_api_key: str = ""
    google_api_key: str = ""
    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False

    # ── Fees ──
    payout_fee_ngn: float = 50.00

    # ── Security ──
    bvn_encryption_key: str = ""
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 7
    # bcrypt work factor. 12 is the minimum we accept outside the test
    # environment - lower values are trivially brute-forced.
    bcrypt_rounds: int = 12

    @field_validator("database_url")
    @classmethod
    def _strip_quotes(cls, v: str) -> str:
        """Pydantic doesn't always strip surrounding quotes from .env values."""
        return v.strip().strip('"').strip("'")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


# ── Startup validation ──────────────────────────────────────────────
#
# Every credential the app needs, and what it is used for. The order is
# the order the operator sees in the error message.
_REQUIRED_SECRETS: tuple[tuple[str, str, str], ...] = (
    ("jwt_secret_key", "JWT_SECRET_KEY", "signs access and refresh tokens"),
    ("bvn_encryption_key", "BVN_ENCRYPTION_KEY", "encrypts BVN records at rest"),
    ("paystack_secret_key", "PAYSTACK_SECRET_KEY", "calls Paystack and verifies webhooks"),
)


def _validate_startup(s: Settings) -> Settings:
    """Fail fast on missing secrets; fill in harmless defaults otherwise.

    In `production` a missing secret is fatal - we would rather not
    start than run with a guessable signing key or a shared test
    credential. Outside production we warn loudly instead, and mint an
    ephemeral JWT key so a developer can still start the app without
    ceremony.
    """
    missing: list[str] = []
    for attr, env_name, purpose in _REQUIRED_SECRETS:
        if not getattr(s, attr).strip():
            missing.append(f"{env_name} - {purpose}")

    if s.webhook_url.strip() and not s.telegram_secret_token.strip():
        missing.append(
            "TELEGRAM_SECRET_TOKEN - authenticates inbound Telegram webhook requests"
        )

    if missing:
        details = "\n  - ".join(missing)
        if s.is_production:
            raise RuntimeError(
                "Refusing to start in production because these settings are "
                f"missing or empty:\n  - {details}\n"
                "Set them in the environment (or .env) and restart."
            )
        logger.warning(
            "Missing settings (the app will start, but these features are "
            "degraded):\n  - %s",
            details,
        )

    if s.bcrypt_rounds < 12 and not s.is_test:
        message = (
            f"BCRYPT_ROUNDS is {s.bcrypt_rounds}; password hashes are "
            "trivially brute-forced below 12."
        )
        if s.is_production:
            raise RuntimeError(message)
        logger.warning("%s", message)

    if not s.jwt_secret_key.strip():
        # Development only: an ephemeral key keeps the app usable without
        # ceremony. It changes on every restart, which invalidates
        # existing tokens - that is the intended trade-off.
        s.jwt_secret_key = secrets.token_urlsafe(64)
        logger.warning(
            "JWT_SECRET_KEY is not set; generated an ephemeral key for this "
            "process. Tokens will not survive a restart."
        )

    return s


@lru_cache
def get_settings() -> Settings:
    """Singleton Settings accessor (cached for the process lifetime)."""
    return _validate_startup(Settings())


# Module-level shortcut for convenience
settings = get_settings()
