"""Bot bill management — the /bills edit and cancel flow.

The handlers are driven directly with fake Update / Context objects,
like `test_telegram_handlers.py`; the DB-backed `session` fixture comes
from the integration conftest.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.security import hash_password
from app.models.bill import Bill
from app.models.user import User


class _FakeMessage:
    def __init__(self, chat_id: int, text: str | None = None) -> None:
        self.chat_id = chat_id
        self.text = text
        self.replies: list[str] = []
        self.edits: list[str] = []
        self.markups: list[object] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class _FakeQuery:
    def __init__(self, chat_id: int, data: str) -> None:
        self.data = data
        self.message = _FakeMessage(chat_id)
        self.answered = 0

    async def answer(self) -> None:
        self.answered += 1

    async def edit_message_text(self, text: str, **kwargs) -> None:
        await self.message.edit_message_text(text, **kwargs)


class _FakeContext:
    def __init__(self) -> None:
        self.user_data: dict = {}


def _callback_update(chat_id: int, data: str):
    query = _FakeQuery(chat_id, data)
    return (
        SimpleNamespace(
            callback_query=query,
            message=None,
            effective_chat=SimpleNamespace(id=chat_id),
        ),
        query,
    )


def _text_update(chat_id: int, text: str):
    msg = _FakeMessage(chat_id, text)
    return SimpleNamespace(
        callback_query=None,
        message=msg,
        effective_chat=SimpleNamespace(id=chat_id),
    ), msg


def _make_user_and_bill(
    session, *, chat_id: int = 55001, status: str = "pending"
) -> tuple[User, Bill]:
    user = User(
        email=f"bm_{chat_id}@x.com",
        hashed_password=hash_password("Secret123"),
        first_name="Bill",
        last_name="Manager",
        phone_number=f"0809{chat_id:08d}",
        telegram_chat_id=str(chat_id),
        is_telegram_linked=True,
        balance=Decimal("50000"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    bill = Bill(
        user_id=user.id,
        vendor_name="DSTV",
        amount=Decimal("5000.00"),
        due_date=datetime.now(tz=UTC) + timedelta(days=5),
        status=status,
        account_number="0123456789",
        bank_code="058",
        bank_name="GTBank",
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return user, bill


@pytest.mark.asyncio
async def test_bills_menu_lists_bills_with_edit_buttons(session) -> None:
    from app.handlers.bill_management import bills_menu

    _user, bill = _make_user_and_bill(session, chat_id=55002)
    update, msg = _text_update(55002, "")

    await bills_menu(update, _FakeContext())

    assert any("Your bills" in r for r in msg.replies)
    assert any(f"#{bill.id}" in r for r in msg.replies)
    assert "DSTV" in msg.replies[0]
    assert msg.markups[-1] is not None


@pytest.mark.asyncio
async def test_editing_amount_updates_the_bill(session) -> None:
    from app.handlers.bill_management import (
        BILL_FIELDS,
        BILL_VALUE,
        handle_edit,
        handle_field,
        handle_value,
    )

    _user, bill = _make_user_and_bill(session, chat_id=55003)
    ctx = _FakeContext()

    update, _query = _callback_update(55003, f"bill_edit:{bill.id}")
    state = await handle_edit(update, ctx)
    assert state == BILL_FIELDS

    update, _query = _callback_update(55003, f"bill_field:{bill.id}:amount")
    state = await handle_field(update, ctx)
    assert state == BILL_VALUE

    update, msg = _text_update(55003, "2,500")
    state = await handle_value(update, ctx)
    assert state == BILL_FIELDS
    assert any("Bill updated" in r for r in msg.replies)

    session.expire_all()
    refreshed = session.get(Bill, bill.id)
    assert Decimal(str(refreshed.amount)) == Decimal("2500.00")


@pytest.mark.asyncio
async def test_invalid_amount_keeps_the_flow_open(session) -> None:
    from app.handlers.bill_management import (
        BILL_VALUE,
        handle_field,
        handle_value,
    )

    _user, bill = _make_user_and_bill(session, chat_id=55004)
    ctx = _FakeContext()

    update, _query = _callback_update(55004, f"bill_field:{bill.id}:amount")
    assert await handle_field(update, ctx) == BILL_VALUE

    update, msg = _text_update(55004, "not-money")
    assert await handle_value(update, ctx) == BILL_VALUE
    assert any("isn't a valid amount" in r for r in msg.replies)

    session.expire_all()
    assert Decimal(str(session.get(Bill, bill.id).amount)) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_editing_a_paid_bill_is_refused(session) -> None:
    from telegram.ext import ConversationHandler

    from app.handlers.bill_management import handle_edit

    _user, bill = _make_user_and_bill(session, chat_id=55005, status="paid")
    update, query = _callback_update(55005, f"bill_edit:{bill.id}")

    assert await handle_edit(update, _FakeContext()) == ConversationHandler.END
    assert any("no longer be edited" in e for e in query.message.edits)


@pytest.mark.asyncio
async def test_cancel_bill_needs_confirmation_then_cancels(session) -> None:
    from telegram.ext import ConversationHandler

    from app.handlers.bill_management import (
        BILL_FIELDS,
        handle_cancel_bill,
        handle_cancel_yes,
    )

    _user, bill = _make_user_and_bill(session, chat_id=55006)

    update, query = _callback_update(55006, f"bill_cancel:{bill.id}")
    assert await handle_cancel_bill(update, _FakeContext()) == BILL_FIELDS
    assert any("Cancel bill" in e for e in query.message.edits)

    update, query = _callback_update(55006, f"bill_cancel_yes:{bill.id}")
    assert await handle_cancel_yes(update, _FakeContext()) == ConversationHandler.END

    session.expire_all()
    assert session.get(Bill, bill.id).status == "cancelled"


@pytest.mark.asyncio
async def test_bank_name_saves_directly_when_unambiguous(session) -> None:
    from app.handlers.bill_management import (
        BILL_FIELDS,
        BILL_VALUE,
        handle_field,
        handle_value,
    )

    _user, bill = _make_user_and_bill(session, chat_id=55007)
    ctx = _FakeContext()

    update, _query = _callback_update(55007, f"bill_field:{bill.id}:bank")
    assert await handle_field(update, ctx) == BILL_VALUE

    update, msg = _text_update(55007, "GTBank")
    assert await handle_value(update, ctx) == BILL_FIELDS
    assert any("Bill updated" in r for r in msg.replies)

    session.expire_all()
    assert session.get(Bill, bill.id).bank_code == "058"


@pytest.mark.asyncio
async def test_ambiguous_bank_name_offers_choices_then_saves_the_pick(
    session,
) -> None:
    from app.handlers.bill_management import (
        BILL_FIELDS,
        BILL_VALUE,
        handle_bank_choice,
        handle_field,
        handle_value,
    )

    _user, bill = _make_user_and_bill(session, chat_id=55008)
    ctx = _FakeContext()

    update, _query = _callback_update(55008, f"bill_field:{bill.id}:bank")
    assert await handle_field(update, ctx) == BILL_VALUE

    # "Trust" is not an exact name, so it must come back as choices.
    update, msg = _text_update(55008, "Trust")
    assert await handle_value(update, ctx) == BILL_VALUE
    markup = msg.markups[-1]
    assert markup is not None
    choices = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("bill_bank:")
    ]
    assert len(choices) >= 2, choices

    update, query = _callback_update(55008, choices[0])
    assert await handle_bank_choice(update, ctx) == BILL_FIELDS

    picked_code = choices[0].rsplit(":", 1)[1]
    session.expire_all()
    assert session.get(Bill, bill.id).bank_code == picked_code
