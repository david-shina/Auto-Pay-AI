"""Integration test fixtures.

These tests hit a real database. The database is always a dedicated
*test* database: the root `tests/conftest.py` resolves it (and refuses
to run against a database whose name does not contain "test") before
any app import, so the engine is bound to it from the start.

DB setup is one-time:
  psql -U postgres -h localhost -d postgres -c "CREATE DATABASE autopay_test;"
  psql -U postgres -h localhost -d autopay_test -f schema.sql
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import TEST_DATABASE_URL

# The root conftest already set DATABASE_URL before any app import, so
# the engine below is bound to the test database. We re-assert it here
# so a mis-ordered import can never point the suite at real data.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Now import the app
import app.core.config as _config_mod
import app.services.crypto as _crypto
from app.core import config as _config

_config.get_settings.cache_clear()
_crypto._fernet.cache_clear()

# IMPORTANT: also refresh the module-level `settings` shortcut. If
# `app.core.config` was imported earlier (e.g. by tests/conftest.py),
# `settings` is still pointing at a stale Settings instance built
# BEFORE we set the env vars. Reassign it now.
_config_mod.settings = _config.get_settings()

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from app import models  # noqa: E402,F401  (registers tables)
from app.core.database import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.payments.base import VirtualAccountData  # noqa: E402

# TABLES to truncate between tests. Order doesn't matter (CASCADE).
TABLES = [
    "audit_logs",
    "refresh_tokens",
    "transactions",
    "bills",
    "virtual_accounts",
    "kyc_records",
    "telegram_link_codes",
    "webhook_events",
    "users",
]


def _assert_test_database(conn) -> None:
    """Guard: only ever truncate a throwaway test database.

    This checks the *connected* database rather than the URL, so a
    mis-parsed DSN or a stray `DATABASE_URL` cannot slip through.
    """
    db_name = conn.execute(text("SELECT current_database()")).scalar_one()
    if "test" not in str(db_name).lower():
        raise RuntimeError(
            f"Refusing to truncate non-test database {db_name!r}. The test "
            "fixtures wipe every table; point TEST_DATABASE_URL at a "
            "database whose name contains 'test'."
        )


def _truncate_all() -> None:
    with engine.connect() as conn:
        _assert_test_database(conn)
        conn.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


@pytest.fixture
def clean_db():
    """Truncate every table before and after the test.

    A normal (not autouse) fixture so it can be requested explicitly by
    the `session` / `client` fixtures, which the unit tests import too.
    """
    _truncate_all()
    yield
    _truncate_all()


@pytest.fixture(autouse=True)
def _autouse_clean_db(clean_db):
    """Every integration test starts from a clean database.

    Autouse in this directory only. The unit tests import `clean_db`
    explicitly, so the tests that touch the database still get a clean
    slate - but the unit tests that never touch it do not pay for a
    truncate they do not need.
    """
    yield



def _install_paystack_stub(monkeypatch=None):
    """Install the Paystack-shaped stub as the payment provider.

    Idempotent: clears any existing DI overrides first, then binds
    the fresh stub. Used by both the autouse `_default_stub_provider`
    fixture and the explicit `stub_provider` fixture so a test can
    request either (or both) and the same stub instance is installed.
    """
    from app.api import auth as auth_module
    from app.api import bills as bills_module
    from app.api import wallet as wallet_module
    from app.api import webhooks as webhooks_module
    from app.services import payments as _payments_module
    from app.services.payments import paystack as _paystack_module

    _StubPaystack._counter = 0
    stub = _StubPaystack()
    stub.calls = []

    # Clear any previous overrides (idempotent / re-bindable).
    for module in (auth_module, bills_module, wallet_module, webhooks_module):
        app.dependency_overrides.pop(module.get_payment_provider, None)

    # FastAPI DI: every Depends(get_payment_provider) returns the stub.
    app.dependency_overrides[auth_module.get_payment_provider] = lambda: stub
    app.dependency_overrides[bills_module.get_payment_provider] = lambda: stub
    app.dependency_overrides[wallet_module.get_payment_provider] = lambda: stub
    app.dependency_overrides[webhooks_module.get_payment_provider] = lambda: stub

    # Function-level patch: scheduler + any non-DI caller.
    if not hasattr(_StubPaystack, "_real_factory"):
        _StubPaystack._real_factory = _paystack_module.get_payment_provider
    if monkeypatch is not None:
        monkeypatch.setattr(
            _payments_module, "get_payment_provider", lambda: stub
        )
    else:
        _payments_module.get_payment_provider = lambda: stub

    return stub


@pytest.fixture(autouse=True)
def _default_stub_provider():
    """Autouse fixture: every integration test gets the Paystack stub
    as the payment provider by default.
    """
    _install_paystack_stub()
    yield
    # The autouse is non-destructive on teardown so `stub_provider`
    # (which uses monkeypatch to override the module-level factory)
    # has its restoration managed by pytest.


@pytest.fixture
def stub_provider(monkeypatch):
    """A fresh Paystack-shaped stub for tests that need to inspect
    `stub.calls` directly.

    The autouse `_default_stub_provider` fixture has already
    installed the DI overrides; this fixture additionally patches
    the module-level factory and re-binds DI in case the test was
    run from a unit-test scope (where the autouse is not active).
    """
    stub = _install_paystack_stub(monkeypatch=monkeypatch)
    yield stub
    # monkeypatch handles restoring `_payments_module.get_payment_provider`


@pytest.fixture
def client(clean_db) -> TestClient:
    """A TestClient with the test DB wired in."""
    return TestClient(app)


@pytest.fixture
def session(clean_db) -> Session:
    """Direct DB session for tests that need to set up data without HTTP."""
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()


# ── Payment provider stub ──────────────────────────────────────────
# Used by every test that hits signup or a bill/payout flow. We don't
# want a real Paystack call; the stub records calls and returns canned
# responses.

class _StubPaystack:
    """Default stub: create customer + DVA, everything else raises."""

    name = "paystack"
    calls: list[tuple[str, dict]] = []
    _counter: int = 0

    async def create_customer(self, **kwargs):
        self.calls.append(("create_customer", kwargs))
        type(self)._counter += 1
        return f"CUS_test_{type(self)._counter}"

    async def create_virtual_account(self, **kwargs):
        self.calls.append(("create_virtual_account", kwargs))
        type(self)._counter += 1
        return VirtualAccountData(
            account_number=f"0123456789{type(self)._counter}",
            account_name=kwargs.get("customer_code", "Test User"),
            bank_name="GTBank",
            bank_code="058",
            provider_reference=f"42_{type(self)._counter}",
            provider="paystack",
        )

    async def resolve_account(self, **kwargs):
        self.calls.append(("resolve_account", kwargs))
        from app.services.payments.base import ResolvedAccount
        return ResolvedAccount(
            account_number=kwargs["account_number"],
            account_name="DSTV NG LTD",
            bank_code=kwargs["bank_code"],
        )

    async def get_transaction(self, **kwargs):
        """Stub for transaction-status lookup. Records the call and
        returns a canned `TransactionStatusResult` with status='success'.
        Used by the manual-verify endpoint and the scheduler poll job.
        Tests that need a 'pending' or 'failed' status can override
        `stub_provider.get_transaction` on a per-test basis.
        """
        from app.services.payments.base import TransactionStatusResult

        self.calls.append(("get_transaction", kwargs))
        return TransactionStatusResult(
            provider_reference=kwargs["reference"],
            status="success",
            amount_kobo=None,
            raw={"status": "success", "stub": True},
        )

    async def create_transfer_recipient(self, **kwargs):
        self.calls.append(("create_transfer_recipient", kwargs))
        return "RCP_test"

    async def initiate_transfer(self, **kwargs):
        self.calls.append(("initiate_transfer", kwargs))
        from app.services.payments.base import TransferResult
        return TransferResult(
            provider_reference=kwargs["reference"],
            provider_transfer_id="99",
            status="pending",
        )

    def verify_webhook_signature(self, **_) -> bool:
        return True

    async def initialize_topup(self, **kwargs):
        """Stub for hosted Checkout top-up. Records the call and
        returns a fake authorization_url."""
        from app.services.payments.base import TopupInit

        self.calls.append(("initialize_topup", kwargs))
        type(self)._counter += 1
        return TopupInit(
            authorization_url=f"https://checkout.paystack.com/test_{type(self)._counter}",
            reference=kwargs["reference"],
            provider="paystack",
            access_code=f"ac_test_{type(self)._counter}",
        )

    async def parse_webhook(self, *, raw_body: bytes, signature_header: str, **kwargs):
        """Stub parse_webhook that actually verifies the signature and
        parses the real body — same as the real provider, but without
        making the network round-trip. This lets webhook tests verify
        the full pipeline (signature → parse → dedup → dispatch →
        credit) end-to-end against the real handler code."""
        import hashlib
        import hmac
        import json as _json

        from app.core.config import settings
        from app.services.payments.base import WebhookEvent

        # Verify signature using the test secret
        expected = hmac.new(
            settings.paystack_secret_key.encode(), raw_body, hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(expected, signature_header or ""):
            from app.services.payments import WebhookSignatureError
            raise WebhookSignatureError(
                "Invalid signature", provider="paystack"
            )

        # Parse the body like the real provider
        try:
            payload = _json.loads(raw_body.decode("utf-8"))
        except (_json.JSONDecodeError, UnicodeDecodeError) as exc:
            from app.services.payments import WebhookSignatureError
            raise WebhookSignatureError(
                f"Webhook body is not valid JSON: {exc}", provider="paystack"
            ) from exc

        event_name = str(payload.get("event") or "")
        data = payload.get("data") or {}
        provider_ref = ""
        amount_kobo = None
        if event_name == "charge.success" or event_name in ("transfer.success", "transfer.failed", "transfer.reversed"):
            provider_ref = str(data.get("reference") or "")
            amount_kobo = int(data.get("amount") or 0)
        else:
            provider_ref = str(data.get("reference") or data.get("id") or "")

        event_id = str(
            payload.get("id")
            or payload.get("event_id")
            or hashlib.sha256(raw_body).hexdigest()
        )

        return WebhookEvent(
            event_type=event_name,
            provider_reference=provider_ref,
            event_id=event_id,
            amount_kobo=amount_kobo,
            raw=payload,
        )
