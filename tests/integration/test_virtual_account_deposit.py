"""Bank transfers into a dedicated virtual account must credit the wallet.

Paystack reports an incoming transfer to a dedicated account as a
`charge.success` webhook that carries the gateway's *customer code*
rather than one of our transaction references. Before this was
handled, the money was dropped as an orphan credit and the user
never saw it in their balance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.enums import TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.models.virtual_account import VirtualAccount

SECRET = get_settings().paystack_secret_key


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()


def _user(session: Session, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password("Secret123"),
        first_name="Ada",
        last_name="L",
        phone_number="0803" + email[:8].rjust(8, "0"),
        balance=Decimal("0"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _virtual_account(session: Session, *, user_id: int) -> VirtualAccount:
    va = VirtualAccount(
        user_id=user_id,
        provider="paystack",
        provider_account_reference="42_1",
        provider_customer_code="CUS_test_1",
        account_number="01234567891",
        account_name="Ada L",
        bank_name="GTBank",
    )
    session.add(va)
    session.commit()
    session.refresh(va)
    return va


def _post_charge(client: TestClient, payload: dict) -> None:
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={
            "x-paystack-signature": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200, resp.text


def test_bank_transfer_to_virtual_account_credits_wallet(
    client: TestClient, session: Session
) -> None:
    user = _user(session, email="dva@x.com")
    _virtual_account(session, user_id=user.id)

    _post_charge(
        client,
        {
            "event": "charge.success",
            "data": {
                "reference": "dva_transfer_1",
                "amount": 250000,
                "customer": "CUS_test_1",
            },
        },
    )

    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("2500.00")

    txn = session.exec(
        select(Transaction).where(
            Transaction.provider_reference == "dva_transfer_1"
        )
    ).one()
    assert txn.type == TransactionType.CREDIT.value
    assert txn.status == TransactionStatus.SUCCESS.value
    assert Decimal(str(txn.amount)) == Decimal("2500.00")
    assert txn.user_id == user.id


def test_bank_transfer_to_unknown_account_stays_an_orphan(
    client: TestClient, session: Session
) -> None:
    """A transfer we cannot attribute to any account must not credit
    anyone - it is recorded as an orphan instead."""
    user = _user(session, email="orphan@x.com")
    _virtual_account(session, user_id=user.id)

    _post_charge(
        client,
        {
            "event": "charge.success",
            "data": {
                "reference": "dva_transfer_2",
                "amount": 75000,
                "customer": "CUS_somebody_else",
            },
        },
    )

    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("0")

    from app.models.audit_log import AuditLog

    rows = session.exec(select(AuditLog)).all()
    assert any(
        r.event_metadata and r.event_metadata.get("status") == "orphan_credit"
        for r in rows
    )


def test_bank_transfer_is_credited_only_once(
    client: TestClient, session: Session
) -> None:
    """A replayed transfer must not credit the wallet twice."""
    user = _user(session, email="dva_twice@x.com")
    _virtual_account(session, user_id=user.id)

    payload = {
        "event": "charge.success",
        "data": {
            "reference": "dva_transfer_3",
            "amount": 100000,
            "customer": "CUS_test_1",
        },
    }
    _post_charge(client, payload)
    _post_charge(client, payload)

    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("1000.00")
