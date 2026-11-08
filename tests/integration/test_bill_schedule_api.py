"""Integration tests for upload-without-payment and POST /bills/{id}/schedule."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.bill import Bill
from app.models.enums import BillStatus
from app.models.transaction import Transaction
from app.models.user import User


def _signup(
    client: TestClient,
    session: Session,
    *,
    email: str,
    phone: str,
    balance: str | None = None,
) -> tuple[str, User]:
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Sched",
            "last_name": "Tester",
            "email": email,
            "phone_number": phone,
            "password": "Secret123",
        },
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]

    user = session.exec(select(User).where(User.email == email)).one()
    if balance is not None:
        user.balance = Decimal(balance)
        session.add(user)
        session.commit()
        session.refresh(user)
    return token, user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_bill(
    client: TestClient,
    token: str,
    *,
    days_ahead: int = 5,
    account_number: str | None = "0123456789",
    bank_code: str | None = "058",
) -> int:
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=UTC) + timedelta(days=days_ahead)).isoformat(),
            "account_number": account_number,
            "bank_code": bank_code,
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["bill"]["id"]


# ── upload creates, never pays ─────────────────────────────────────


def test_upload_recommends_but_does_not_pay(
    client: TestClient, session: Session, stub_provider
) -> None:
    """An upload must not move money: it returns a recommendation and the
    caller decides via /pay or /schedule."""
    token, user = _signup(
        client, session, email="upload1@x.com", phone="08031110001", balance="100000"
    )

    r = client.post(
        "/api/v1/bills/upload",
        data={"request_bill": "Pay MTN 5000 tomorrow"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["bill"]["status"] == "pending"
    # Sufficient balance + due tomorrow => the agent recommends paying now,
    # but that is only a recommendation.
    assert body["decision"] == "pay_now"
    assert "pay it now" in body["message"].lower()

    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("100000")
    debits = session.exec(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.type == "debit",
        )
    ).all()
    assert debits == []


# ── POST /bills/{id}/schedule ──────────────────────────────────────


def test_schedule_defers_the_bill(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(
        client, session, email="sched1@x.com", phone="08031110002"
    )
    bill_id = _create_bill(client, token)

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bill"]["status"] == "scheduled"
    assert body["bill"]["is_recurring"] is False


def test_schedule_applies_recurrence(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(
        client, session, email="sched2@x.com", phone="08031110003"
    )
    bill_id = _create_bill(client, token)

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={"recurrence_interval": "monthly"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert "repeating monthly" in r.json()["message"]

    bill = session.get(Bill, bill_id)
    session.refresh(bill)
    assert bill.status == BillStatus.SCHEDULED.value
    assert bill.is_recurring is True
    assert bill.recurrence_interval == "monthly"
    assert bill.next_recurrence_date is not None


def test_schedule_recurring_needs_a_payout_destination(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(
        client, session, email="sched3@x.com", phone="08031110004"
    )
    bill_id = _create_bill(
        client, token, account_number=None, bank_code=None
    )

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={"recurrence_interval": "weekly"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text
    assert "account number" in r.json()["detail"].lower()


def test_schedule_can_move_the_due_date(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(
        client, session, email="sched4@x.com", phone="08031110005"
    )
    bill_id = _create_bill(client, token, days_ahead=2)
    new_due = (datetime.now(tz=UTC) + timedelta(days=20)).replace(microsecond=0)

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={"due_date": new_due.isoformat()},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    bill = session.get(Bill, bill_id)
    session.refresh(bill)
    assert bill.due_date.date() == new_due.date()


def test_schedule_rejects_a_paid_bill(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(
        client, session, email="sched5@x.com", phone="08031110006"
    )
    bill_id = _create_bill(client, token)

    bill = session.get(Bill, bill_id)
    bill.status = BillStatus.PAID.value
    session.add(bill)
    session.commit()

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={},
        headers=_auth(token),
    )
    assert r.status_code == 409, r.text


def test_schedule_is_owner_scoped(
    client: TestClient, session: Session, stub_provider
) -> None:
    owner_token, _owner = _signup(
        client, session, email="sched6@x.com", phone="08031110007"
    )
    bill_id = _create_bill(client, owner_token)

    other_token, _other = _signup(
        client, session, email="sched7@x.com", phone="08031110008"
    )

    r = client.post(
        f"/api/v1/bills/{bill_id}/schedule",
        json={},
        headers=_auth(other_token),
    )
    assert r.status_code == 404, r.text
