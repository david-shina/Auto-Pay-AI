"""Integration tests for OTP-parked transfers and reconciliation.

Covers the three ways a payout can fail to become terminal:
  * Paystack parks the transfer awaiting OTP (`status: "otp"`)
  * the success webhook is lost or races the payout commit
  * a parked OTP is never approved
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import scheduler as scheduler_module
from app.models.bill import Bill
from app.models.enums import BillStatus, TransactionStatus
from app.models.transaction import Transaction
from app.models.user import User
from app.services.payments import ProviderError
from app.services.payments.base import TransactionStatusResult, TransferResult
from app.services.payout import execute_payout


def _make_user(session: Session, balance: str = "1000.00") -> User:
    user = User(
        first_name="Otp",
        last_name="Tester",
        email=f"otp_{time.time_ns()}@x.com",
        phone_number=f"+1{str(time.time_ns())[-10:]}",
        hashed_password="x",
        balance=Decimal(balance),
        currency="NGN",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_bill(session: Session, user: User, *, status: str = "pending") -> Bill:
    bill = Bill(
        user_id=user.id,
        vendor_name="DSTV NG LTD",  # must match the stub's resolved name
        amount=Decimal("500.00"),
        currency="NGN",
        due_date=datetime.now() + timedelta(days=1),
        account_number="0123456789",
        bank_code="058",
        status=status,
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return bill


def _otp_transfer(**kwargs) -> TransferResult:
    return TransferResult(
        provider_reference=kwargs["reference"],
        provider_transfer_id="1",
        status="otp",
        transfer_code="TRF_test",
    )


# ── execute_payout: otp branch ─────────────────────────────────────

async def test_execute_payout_marks_the_debit_as_awaiting_otp(
    session: Session, stub_provider, monkeypatch
) -> None:
    user = _make_user(session)
    bill = _make_bill(session, user)

    async def _initiate(**kwargs):
        stub_provider.calls.append(("initiate_transfer", kwargs))
        return _otp_transfer(**kwargs)

    monkeypatch.setattr(stub_provider, "initiate_transfer", _initiate)

    result = await execute_payout(session, bill_id=bill.id, provider=stub_provider)
    session.commit()

    assert result.requires_otp is True
    assert "OTP" in result.message

    txn = session.exec(
        select(Transaction).where(
            Transaction.bill_id == bill.id,
            Transaction.type == "debit",
        )
    ).one()
    assert txn.status == TransactionStatus.PROCESSING.value
    assert txn.provider_transfer_code == "TRF_test"

    # Wallet is debited (amount + 50 fee) and the bill stays processing.
    session.refresh(user)
    session.refresh(bill)
    assert Decimal(str(user.balance)) == Decimal("450.00")
    assert bill.status == BillStatus.PROCESSING.value


async def test_otp_without_a_transfer_code_is_refunded(
    session: Session, stub_provider, monkeypatch
) -> None:
    user = _make_user(session)
    bill = _make_bill(session, user)

    async def _initiate(**kwargs):
        return TransferResult(
            provider_reference=kwargs["reference"],
            provider_transfer_id="1",
            status="otp",
            transfer_code=None,  # cannot finalize
        )

    monkeypatch.setattr(stub_provider, "initiate_transfer", _initiate)

    with pytest.raises(HTTPException):
        await execute_payout(session, bill_id=bill.id, provider=stub_provider)
    session.rollback()

    # No money moved: the debit was marked failed before the balance change.
    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("1000.00")
    txn = session.exec(
        select(Transaction).where(Transaction.bill_id == bill.id, Transaction.type == "debit")
    ).one()
    assert txn.status == TransactionStatus.FAILED.value


# ── POST /bills/{id}/transfer-otp ─────────────────────────────────

def _signup(client: TestClient) -> tuple[str, int]:
    stamp = time.time_ns()
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Otp",
            "last_name": "Api",
            "email": f"otpapi_{stamp}@x.com",
            "phone_number": f"+1{str(stamp)[-10:]}",
            "password": "Secret123",
        },
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


def _pending_otp_txn(session: Session, user_id: int, bill_id: int) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        bill_id=bill_id,
        type="debit",
        amount=Decimal("500.00"),
        fee=Decimal("50.00"),
        currency="NGN",
        status=TransactionStatus.PROCESSING.value,
        provider="paystack",
        provider_reference=f"autopay_{bill_id}_otp",
        provider_transfer_code="TRF_test",
    )
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def test_transfer_otp_completes_the_payout(client: TestClient, session: Session) -> None:
    token, user_id = _signup(client)
    bill = _make_bill(session, session.get(User, user_id), status=BillStatus.PROCESSING.value)
    _pending_otp_txn(session, user_id, bill.id)

    r = client.post(
        f"/api/v1/bills/{bill.id}/transfer-otp",
        json={"otp": "123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["bill"]["status"] == BillStatus.PAID.value

    txn = session.exec(
        select(Transaction).where(Transaction.bill_id == bill.id, Transaction.type == "debit")
    ).one()
    session.refresh(txn)
    assert txn.status == TransactionStatus.SUCCESS.value


def test_transfer_otp_with_nothing_pending_is_404(
    client: TestClient, session: Session
) -> None:
    token, user_id = _signup(client)
    bill = _make_bill(session, session.get(User, user_id))

    r = client.post(
        f"/api/v1/bills/{bill.id}/transfer-otp",
        json={"otp": "123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404, r.text


def test_rejected_otp_does_not_refund(
    client: TestClient, session: Session, stub_provider, monkeypatch
) -> None:
    token, user_id = _signup(client)
    bill = _make_bill(session, session.get(User, user_id), status=BillStatus.PROCESSING.value)
    txn = _pending_otp_txn(session, user_id, bill.id)

    async def _reject(**kwargs):
        raise ProviderError("Invalid OTP", provider="paystack")

    monkeypatch.setattr(stub_provider, "finalize_transfer", _reject)

    r = client.post(
        f"/api/v1/bills/{bill.id}/transfer-otp",
        json={"otp": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text

    session.refresh(txn)
    assert txn.status == TransactionStatus.PROCESSING.value


# ── reconciliation job ────────────────────────────────────────────

def _aged_processing_txn(
    session: Session, user_id: int, bill_id: int, *, minutes: int, otp: bool
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        bill_id=bill_id,
        type="debit",
        amount=Decimal("500.00"),
        fee=Decimal("50.00"),
        currency="NGN",
        status=TransactionStatus.PROCESSING.value,
        provider="paystack",
        provider_reference=f"autopay_{bill_id}_aged",
        provider_transfer_code="TRF_test" if otp else None,
    )
    session.add(txn)
    session.commit()
    # Backdate so the sweep picks it up.
    txn.created_at = datetime.now() - timedelta(minutes=minutes)
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


async def test_reconcile_settles_a_transfer_the_webhook_missed(
    session: Session, stub_provider
) -> None:
    user = _make_user(session)
    bill = _make_bill(session, user, status=BillStatus.PROCESSING.value)
    txn = _aged_processing_txn(session, user.id, bill.id, minutes=10, otp=False)

    # Stub get_transfer defaults to "success".
    await scheduler_module._reconcile_processing_transfers()

    session.refresh(txn)
    session.refresh(bill)
    assert txn.status == TransactionStatus.SUCCESS.value
    assert bill.status == BillStatus.PAID.value


async def test_reconcile_refunds_a_failed_transfer(
    session: Session, stub_provider, monkeypatch
) -> None:
    user = _make_user(session)
    bill = _make_bill(session, user, status=BillStatus.PROCESSING.value)
    txn = _aged_processing_txn(session, user.id, bill.id, minutes=10, otp=False)

    # The debit was applied by execute_payout; simulate that here.
    user.balance = Decimal(str(user.balance)) - Decimal("550.00")
    session.add(user)
    session.commit()

    async def _failed(**kwargs):
        return TransactionStatusResult(
            provider_reference=kwargs["reference"], status="failed"
        )

    monkeypatch.setattr(stub_provider, "get_transfer", _failed)

    await scheduler_module._reconcile_processing_transfers()

    session.refresh(txn)
    session.refresh(user)
    assert txn.status == TransactionStatus.FAILED.value
    assert Decimal(str(user.balance)) == Decimal("1000.00")  # refunded amount + fee


async def test_reconcile_refunds_an_unapproved_otp_after_the_ttl(
    session: Session, stub_provider, monkeypatch
) -> None:
    user = _make_user(session)
    bill = _make_bill(session, user, status=BillStatus.PROCESSING.value)
    txn = _aged_processing_txn(session, user.id, bill.id, minutes=20, otp=True)

    user.balance = Decimal(str(user.balance)) - Decimal("550.00")
    session.add(user)
    session.commit()

    async def _still_pending(**kwargs):
        return TransactionStatusResult(
            provider_reference=kwargs["reference"], status="pending"
        )

    monkeypatch.setattr(stub_provider, "get_transfer", _still_pending)

    await scheduler_module._reconcile_processing_transfers()

    session.refresh(txn)
    session.refresh(user)
    assert txn.status == TransactionStatus.FAILED.value
    assert txn.failure_reason == "otp_expired"
    assert Decimal(str(user.balance)) == Decimal("1000.00")
