"""Shared pytest fixtures + test env setup.

This conftest runs before any test module is imported. We:

  1. Point `DATABASE_URL` at a dedicated **test** database. The
     integration fixtures TRUNCATE every application table between
     tests, so this must never resolve to a database whose contents
     matter. We refuse to run against a database whose name does not
     contain "test".
  2. Set a known JWT secret + generate a valid Fernet key.
  3. Clear the cached Settings + Fernet singletons so they pick up
     the env values we just set.
  4. Provide shared fixtures.

The test database can be overridden with `TEST_DATABASE_URL`.
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Pick up TEST_DATABASE_URL (and anything else) from .env, but never
# let .env override a variable that is already in the environment.
load_dotenv(override=False)

# ── Test database ───────────────────────────────────────────────────
DEFAULT_TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/autopay_test"


def _resolve_test_db_url() -> str:
    """Pick the database the suite is allowed to truncate.

    `TEST_DATABASE_URL` wins when set. Otherwise we fall back to the
    conventional local test database. Either way the database name
    must contain "test" — the suite wipes every table, so pointing it
    at `autopay` would destroy real data.
    """
    url = os.environ.get("TEST_DATABASE_URL", "").strip() or DEFAULT_TEST_DB_URL
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0].strip().lower()
    if "test" not in db_name:
        raise RuntimeError(
            f"Refusing to run the test suite against database {db_name!r}. "
            "The integration fixtures TRUNCATE every table, so the target "
            "database must be a throwaway whose name contains 'test'. "
            "Set TEST_DATABASE_URL to such a database."
        )
    return url


TEST_DATABASE_URL = _resolve_test_db_url()

# `tests/manual/` holds interactive scripts (they drive a browser and
# need Node.js), not automated tests. Do not collect them.
collect_ignore_glob = ["manual/*"]

# ── Set env vars BEFORE any `from app...` import below ─────────────
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-jwt-secret-must-be-at-least-32-chars-long"
)
os.environ.setdefault("BVN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy")
# bcrypt cost 12 takes ~1s per hash on a laptop CPU, which dominated
# the runtime of the suite. The hashes are throwaway, so we lower the
# work factor here (the config validator rejects this outside `test`).
os.environ.setdefault("BCRYPT_ROUNDS", "4")
# Force the regex fallback in the loaders (no real LLM calls). Some
# tests assert that the endpoint returns 422 when the LLM can't pull
# a vendor from the input. Without this override those tests would
# silently switch to LLM-extracted values.
os.environ["GROQ_API_KEY"] = ""
# Never talk to a real Telegram bot or a real webhook URL from tests.
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_SECRET_TOKEN"] = ""
os.environ["WEBHOOK_URL"] = ""
# Background auto-pay jobs must not run while tests assert on balances.
os.environ["SCHEDULER_ENABLED"] = "false"

# Now we can import app modules safely
from app.core import config as _config  # noqa: E402

# Clear any cached settings from a prior import
_config.get_settings.cache_clear()

import app.services.crypto as _crypto  # noqa: E402

_crypto._fernet.cache_clear()


@pytest.fixture
def sample_user_dict() -> dict:
    """Placeholder factory for tests that land in Chunk 2."""
    return {
        "email": "test@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
