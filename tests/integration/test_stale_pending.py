"""Integration tests for the stale-pending sweep.

A bill created without an explicit intent waits in `pending`. If nobody
chooses, the sweep must give it a money-safe outcome: schedule it, nudge
the user, or cancel it - never pay it.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.core import scheduler as scheduler_module
from app.models.audit_log import AuditLog
from app.models.bill import Bill
from app.models.enums import AuditEventType, BillStatus
from app.models.transaction import Transaction
from app.models.user import User


def _user(session: Session, *, balance: str = "100000.00") -> User:
    user = User(
        first_name="Stale",
        last_name="Tester",
        email=f"stale_{time.time_ns()}@x.com",
        phone_number=f"+1{str(time.time_ns())[-10:]}",
        hashed_password="x",
        balance=Decimal(balance),
        currency="NGN",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _bill(
    session: Session,
    user: User,
    *,
    age_hours: int,
    due_in_days: int,
    account_number: str | None = "0123456789",
    bank_code: str | None = "058",
    amount: str = "5000.00",
) -> Bill:
    bill = Bill(
        user_id=user.id,
        vendor_name="DSTV",
        amount=Decimal(amount),
        currency="NGN",
        due_date=datetime.now() + timedelta(days=due_in_days),
        account_number=account_number,
        bank_code=bank_code,
        status=BillStatus.PENDING.value,
    )
    session.add(bill)
    session.commit()
    # Backdate so the sweep considers it stale.
    bill.created_at = datetime.now() - timedelta(hours=age_hours)
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return bill


def _nudges(session: Session, bill_id: int) -> list[AuditLog]:
    return list(
        session.exec(
            select(AuditLog).where(
                AuditLog.event_type == AuditEventType.BILL_DECISION_NUDGE.value,
                AuditLog.entity_id == bill_id,
            )
        ).all()
    )


def _debits(session: Session, user_id: int) -> list[Transaction]:
    return list(
        session.exec(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.type == "debit",
            )
        ).all()
    )


async def test_auto_schedules_a_future_bill_the_agent_would_schedule(
    session: Session,
) -> None:
    user = _user(session)
    bill = _bill(session, user, age_hours=25, due_in_days=10)

    await scheduler_module._resolve_stale_pending_bills()

    session.refresh(bill)
    assert bill.status == BillStatus.SCHEDULED.value
    # Deferring must never move money.
    assert _debits(session, user.id) == []
    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("100000.00")


async def test_does_not_schedule_an_overdue_bill(session: Session) -> None:
    """Scheduling an already-due bill would be paid on the next tick -
    that is an unapproved payment, so it is only nudged."""
    user = _user(session)
    bill = _bill(session, user, age_hours=25, due_in_days=-1)

    await scheduler_module._resolve_stale_pending_bills()

    session.refresh(bill)
    assert bill.status == BillStatus.PENDING.value
    assert len(_nudges(session, bill.id)) == 1
    assert _debits(session, user.id) == []


async def test_does_not_schedule_without_a_payout_destination(
    session: Session,
) -> None:
    user = _user(session)
    bill = _bill(
        session, user, age_hours=25, due_in_days=10, bank_code=None
    )

    await scheduler_module._resolve_stale_pending_bills()

    session.refresh(bill)
    assert bill.status == BillStatus.PENDING.value
    assert len(_nudges(session, bill.id)) == 1


async def test_nudges_only_once(session: Session) -> None:
    user = _user(session, balance="0.00")  # agent says HOLD -> nudge
    bill = _bill(session, user, age_hours=25, due_in_days=10)

    await scheduler_module._resolve_stale_pending_bills()
    await scheduler_module._resolve_stale_pending_bills()

    assert len(_nudges(session, bill.id)) == 1


async def test_cancels_a_bill_after_the_expiry_window(session: Session) -> None:
    user = _user(session)
    bill = _bill(session, user, age_hours=24 * 8, due_in_days=10)

    await scheduler_module._resolve_stale_pending_bills()

    session.refresh(bill)
    assert bill.status == BillStatus.CANCELLED.value
    assert _debits(session, user.id) == []

    cancelled = session.exec(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.BILL_CANCELLED.value,
            AuditLog.entity_id == bill.id,
        )
    ).first()
    assert cancelled is not None
    assert cancelled.event_metadata["reason"] == "no_decision"


async def test_ignores_recent_and_decided_bills(session: Session) -> None:
    user = _user(session)
    recent = _bill(session, user, age_hours=1, due_in_days=10)
    scheduled = _bill(session, user, age_hours=25, due_in_days=10)
    scheduled.status = BillStatus.SCHEDULED.value
    session.add(scheduled)
    session.commit()

    await scheduler_module._resolve_stale_pending_bills()

    session.refresh(recent)
    session.refresh(scheduled)
    assert recent.status == BillStatus.PENDING.value
    assert scheduled.status == BillStatus.SCHEDULED.value
    assert _nudges(session, recent.id) == []
