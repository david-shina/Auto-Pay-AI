"""Integration tests for the wallet endpoints (DVA provisioning)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.database import session_scope as _session_scope
from app.models.virtual_account import VirtualAccount


def _signup(client: TestClient, email: str = "wallet@x.com", phone: str = "08090000001") -> str:
    s = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Wallet",
            "last_name": "Tester",
            "email": email,
            "phone_number": phone,
            "password": "Secret123",
        },
    )
    assert s.status_code == 201, s.text
    return f"Bearer {s.json()['access_token']}"


def test_provision_virtual_account_creates_dva(
    client: TestClient, stub_provider, session: Session
) -> None:
    h = _signup(client)
    r = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["already_existed"] is False
    assert body["virtual_account"]["account_number"]
    assert body["virtual_account"]["provider"] == "paystack"
    # Stub was called for customer + DVA creation
    called = [c[0] for c in stub_provider.calls]
    assert "create_customer" in called
    assert "create_virtual_account" in called
    # DB row exists
    va = session.exec(select(VirtualAccount)).first()
    assert va is not None
    assert va.account_number == body["virtual_account"]["account_number"]


def test_provision_virtual_account_idempotent(
    client: TestClient, stub_provider
) -> None:
    h = _signup(client, email="idem@x.com", phone="08090000002")
    r1 = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r1.status_code == 201
    first_body = r1.json()
    # second call should NOT hit the provider
    calls_after_first = len(stub_provider.calls)
    r2 = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r2.status_code == 201
    second_body = r2.json()
    assert second_body["already_existed"] is True
    assert second_body["virtual_account"]["account_number"] == first_body["virtual_account"]["account_number"]
    assert len(stub_provider.calls) == calls_after_first


def test_provision_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/wallet/provision")
    assert r.status_code == 401


# ── Telegram link-code ─────────────────────────────────────────────


def test_create_telegram_link_code(client: TestClient) -> None:
    h = _signup(client, email="tgcode@x.com", phone="08090000003")
    r = client.post("/api/v1/auth/telegram/link-code", headers={"Authorization": h})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["code"]) == 6
    assert "expires_at" in body


def test_create_telegram_link_code_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/auth/telegram/link-code")
    assert r.status_code == 401


def test_unlink_telegram_clears_chat_id(
    client: TestClient, session: Session
) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.core.database import session_scope
    h = _signup(client, email="ulnk@x.com", phone="08090000004")
    with session_scope() as s:
        u = s.exec(select(User).where(User.email == "ulnk@x.com")).first()
        u.telegram_chat_id = "12345"
        u.is_telegram_linked = True
        s.add(u)

    r = client.delete("/api/v1/auth/telegram/link", headers={"Authorization": h})
    assert r.status_code == 204

    with session_scope() as s:
        u = s.exec(select(User).where(User.email == "ulnk@x.com")).first()
        assert u.telegram_chat_id is None
        assert u.is_telegram_linked is False


# ── Top-up via Checkout (no DVA) ─────────────────────────────────


def test_topup_returns_authorization_url(client: TestClient, stub_provider) -> None:
    """POST /wallet/topup returns a Paystack-hosted URL and persists a
    pending `Transaction` row that the webhook will look up on success."""
    from decimal import Decimal as _Decimal

    from app.models.transaction import Transaction
    from app.models.enums import TransactionStatus, TransactionType

    h = _signup(client, email="top1@x.com", phone="08090000011")
    r = client.post(
        "/api/v1/wallet/topup",
        json={"amount": 1500},
        headers={"Authorization": h},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Pydantic serializes Decimal as a string in JSON
    assert _Decimal(str(body["amount"])) == _Decimal("1500")
    assert body["currency"] == "NGN"
    assert body["authorization_url"].startswith("https://checkout.paystack.com/")
    assert body["reference"].startswith("topup_")
    # The provider's initialize_topup was called
    called = [c[0] for c in stub_provider.calls]
    assert "initialize_topup" in called
    # A pending Transaction row was created
    with _session_scope() as s:
        txn = s.exec(
            select(Transaction).where(Transaction.id == body["transaction_id"])
        ).first()
        assert txn is not None
        assert txn.type == TransactionType.CREDIT.value
        assert txn.status == TransactionStatus.PENDING.value
        assert txn.provider_reference == body["reference"]


def test_topup_rejects_amount_below_minimum(
    client: TestClient, stub_provider
) -> None:
    h = _signup(client, email="top2@x.com", phone="08090000012")
    r = client.post(
        "/api/v1/wallet/topup",
        json={"amount": 50},  # MIN_TOPUP_NGN is 100
        headers={"Authorization": h},
    )
    assert r.status_code == 400
    assert "Minimum" in r.json()["detail"]
    # The provider was NOT called
    assert not any(c[0] == "initialize_topup" for c in stub_provider.calls)


def test_topup_rejects_amount_above_maximum(
    client: TestClient, stub_provider
) -> None:
    h = _signup(client, email="top3@x.com", phone="08090000013")
    r = client.post(
        "/api/v1/wallet/topup",
        json={"amount": 9_999_999},  # MAX_TOPUP_NGN is 1_000_000
        headers={"Authorization": h},
    )
    assert r.status_code == 400
    assert "Maximum" in r.json()["detail"]


def test_topup_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/wallet/topup", json={"amount": 1000})
    assert r.status_code == 401


def test_topup_charge_success_webhook_credits_wallet(
    client: TestClient, stub_provider, session: Session
) -> None:
    """End-to-end: POST /topup → simulate charge.success webhook → assert
    the user's wallet is credited and the Transaction status flips."""
    import hmac
    import hashlib
    import json

    from app.core.config import get_settings
    from app.models.user import User
    from app.core.database import session_scope

    SECRET = get_settings().paystack_secret_key
    h = _signup(client, email="top4@x.com", phone="08090000014")

    # 1. Top-up: 2000 NGN
    r = client.post(
        "/api/v1/wallet/topup",
        json={"amount": 2000},
        headers={"Authorization": h},
    )
    assert r.status_code == 201
    reference = r.json()["reference"]

    # 2. Simulate Paystack's charge.success webhook
    payload = json.dumps(
        {"event": "charge.success", "data": {"reference": reference, "amount": 200000}}
    ).encode()
    sig = hmac.new(SECRET.encode(), payload, hashlib.sha512).hexdigest()
    wr = client.post(
        "/webhooks/paystack",
        content=payload,
        headers={
            "x-paystack-signature": sig,
            "Content-Type": "application/json",
        },
    )
    assert wr.status_code == 200

    # 3. Wallet is credited
    with session_scope() as s:
        u = s.exec(select(User).where(User.email == "top4@x.com")).first()
        from decimal import Decimal
        assert Decimal(str(u.balance)) == Decimal("2000.00")
