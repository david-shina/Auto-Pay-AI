"""Integration tests for editing a bill (PATCH /api/v1/bills/{id})."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.database import engine
from app.models.bill import Bill
from app.models.enums import BillStatus
from app.models.user import User
from app.services.bill import BillNotEditableError, update_bill


def _signup(client: TestClient, session: Session, email: str, phone: str) -> tuple[str, User]:
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Edit",
            "last_name": "Tester",
            "email": email,
            "phone_number": phone,
            "password": "Secret123",
        },
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    user = session.exec(select(User).where(User.email == email)).one()
    return token, user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_bill(
    client: TestClient,
    token: str,
    *,
    account_number: str | None = "0123456789",
    bank_code: str | None = "058",
) -> int:
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=UTC) + timedelta(days=5)).isoformat(),
            "account_number": account_number,
            "bank_code": bank_code,
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["bill"]["id"]


def test_patch_updates_the_basic_fields(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit1@x.com", "08032220001")
    bill_id = _create_bill(client, token)
    new_due = (datetime.now(tz=UTC) + timedelta(days=12)).replace(microsecond=0)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={
            "vendor_name": "GOTV",
            "amount": 2500,
            "due_date": new_due.isoformat(),
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vendor_name"] == "GOTV"
    assert Decimal(str(body["amount"])) == Decimal("2500")
    assert body["due_date"].startswith(new_due.date().isoformat())


def test_patch_resolves_a_bank_supplied_by_name(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit2@x.com", "08032220002")
    bill_id = _create_bill(client, token)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"bank_name": "Access Bank"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["bank_code"] == "044"
    assert r.json()["bank_name"] == "Access Bank"


def test_patch_sets_and_clears_recurrence(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit3@x.com", "08032220003")
    bill_id = _create_bill(client, token)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"recurrence_interval": "monthly"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_recurring"] is True

    bill = session.get(Bill, bill_id)
    session.refresh(bill)
    assert bill.recurrence_interval == "monthly"
    assert bill.next_recurrence_date is not None

    # Explicit null turns it back into a one-off.
    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"recurrence_interval": None},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_recurring"] is False

    session.refresh(bill)
    assert bill.next_recurrence_date is None


def test_patch_recurrence_without_a_destination_is_422(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit4@x.com", "08032220004")
    bill_id = _create_bill(client, token, account_number=None, bank_code=None)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"recurrence_interval": "weekly"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text
    assert "recurring" in r.json()["detail"].lower()


def test_patch_amount_out_of_range_is_422(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit5@x.com", "08032220005")
    bill_id = _create_bill(client, token)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"amount": 50},  # below MIN_BILL_NGN
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text
    assert "between" in r.json()["detail"].lower()


def test_patch_invalid_account_number_is_422(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit6@x.com", "08032220006")
    bill_id = _create_bill(client, token)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"account_number": "123"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text
    assert "10 or 11 digits" in r.json()["detail"]


def test_patch_unknown_bank_is_422(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit7@x.com", "08032220007")
    bill_id = _create_bill(client, token)

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"bank_name": "ZZZQQQ Not A Bank"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


def test_patch_terminal_bills_are_409(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit8@x.com", "08032220008")

    for status in (BillStatus.PAID.value, BillStatus.CANCELLED.value):
        bill_id = _create_bill(client, token)
        bill = session.get(Bill, bill_id)
        bill.status = status
        session.add(bill)
        session.commit()

        r = client.patch(
            f"/api/v1/bills/{bill_id}",
            json={"amount": 3000},
            headers=_auth(token),
        )
        assert r.status_code == 409, (status, r.text)


def test_editing_a_failed_bill_rearms_it(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit9@x.com", "08032220009")
    bill_id = _create_bill(client, token)
    bill = session.get(Bill, bill_id)
    bill.status = BillStatus.FAILED.value
    bill.retry_count = bill.max_retries
    session.add(bill)
    session.commit()

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"due_date": (datetime.now(tz=UTC) + timedelta(days=9)).isoformat()},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    session.refresh(bill)
    assert bill.retry_count == 0
    # Back in the scheduler's queue, otherwise nobody ever pays it.
    assert bill.status == BillStatus.SCHEDULED.value


def test_vendor_edit_keeps_the_recurrence_clock(
    client: TestClient, session: Session, stub_provider
) -> None:
    token, _user = _signup(client, session, "edit12@x.com", "08032220012")
    bill_id = _create_bill(client, token)

    bill = session.get(Bill, bill_id)
    advanced = (datetime.now(tz=UTC) + timedelta(days=20)).replace(microsecond=0)
    bill.is_recurring = True
    bill.recurrence_interval = "monthly"
    bill.next_recurrence_date = advanced
    session.add(bill)
    session.commit()

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"vendor_name": "GOTV"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    session.refresh(bill)
    assert bill.next_recurrence_date == advanced, (
        "an unrelated edit must not rewind the next-recurrence date"
    )


def test_update_bill_rechecks_status_after_a_payment(
    client: TestClient, session: Session, stub_provider
) -> None:
    """A payout can commit between the caller's read and the edit's
    write. The shared service must re-read under a lock and refuse."""
    token, _user = _signup(client, session, "edit13@x.com", "08032220013")
    bill_id = _create_bill(client, token)

    stale = session.get(Bill, bill_id)  # still pending in this session
    with Session(engine) as other:
        paid = other.get(Bill, bill_id)
        paid.status = BillStatus.PAID.value
        other.add(paid)
        other.commit()

    with pytest.raises(BillNotEditableError):
        update_bill(session, bill=stale, changes={"amount": 9000})


def test_patch_is_owner_scoped(
    client: TestClient, session: Session, stub_provider
) -> None:
    owner_token, _owner = _signup(client, session, "edit10@x.com", "08032220010")
    bill_id = _create_bill(client, owner_token)
    other_token, _other = _signup(client, session, "edit11@x.com", "08032220011")

    r = client.patch(
        f"/api/v1/bills/{bill_id}",
        json={"amount": 3000},
        headers=_auth(other_token),
    )
    assert r.status_code == 404, r.text
