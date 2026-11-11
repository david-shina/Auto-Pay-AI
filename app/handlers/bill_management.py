"""Bot bill management — `/bills` listing with inline Edit / Cancel.

`/bills` used to be a read-only list in `auth.py`. Editing a bill means
changing money-moving data, so the flow gets its own ConversationHandler
and reuses `app.services.bill.update_bill` / `cancel_bill` — the API and
the bot share one validation path.

States:
  BILL_FIELDS : a bill is selected; tap a field, cancel it, or go back
  BILL_VALUE  : waiting for the typed value of the selected field
"""
from __future__ import annotations

import contextlib
import logging
from decimal import Decimal, InvalidOperation

from sqlmodel import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.core.database import session_scope
from app.handlers.helpers import (
    escape_md,
    get_linked_user,
    parse_user_date,
    safe_edit,
)
from app.models.bill import Bill
from app.models.user import User
from app.services.bill import (
    EDITABLE_STATUSES,
    BillNotEditableError,
    BillValidationError,
    cancel_bill,
    update_bill,
)

logger = logging.getLogger(__name__)

BILL_FIELDS, BILL_VALUE = range(2)

_FIELDS = ("vendor", "amount", "due", "account", "bank")


# ── Views ──────────────────────────────────────────────────────────


def _view(bill: Bill) -> dict:
    """Copy the fields the bot needs while the session is still open."""
    return {
        "id": bill.id,
        "vendor_name": bill.vendor_name,
        "amount": bill.amount,
        "due_date": bill.due_date,
        "account_number": bill.account_number,
        "bank_name": bill.bank_name,
        "bank_code": bill.bank_code,
        "status": bill.status,
        "is_recurring": bill.is_recurring,
        "recurrence_interval": bill.recurrence_interval,
    }


def _amount(value) -> str:
    try:
        return f"₦{Decimal(str(value)):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "N/A"


def _due(bill: dict) -> str:
    return bill["due_date"].date().isoformat() if bill["due_date"] else "?"


def _load_bill(user_id: int, bill_id: int) -> dict | None:
    with session_scope() as session:
        bill = session.get(Bill, bill_id)
        if bill is None or bill.user_id != user_id:
            return None
        return _view(bill)


def _load_bills(user_id: int) -> list[dict]:
    with session_scope() as session:
        rows = session.exec(
            select(Bill)
            .where(Bill.user_id == user_id)
            .order_by(Bill.due_date.asc())
            .limit(20)
        ).all()
        return [_view(b) for b in rows]


# ── Keyboards & text ───────────────────────────────────────────────


def _menu_keyboard(bills: list[dict]) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(
                f"✏️ #{b['id']} {b['vendor_name'][:22]}",
                callback_data=f"bill_edit:{b['id']}",
            )
        ]
        for b in bills
        if b["status"] in EDITABLE_STATUSES
    ]
    return InlineKeyboardMarkup(rows) if rows else None


def _menu_text(bills: list[dict]) -> str:
    lines = ["📄 *Your bills*\n"]
    for b in bills:
        recur = " 🔁" if b["is_recurring"] else ""
        lines.append(
            f"*#{b['id']}* {escape_md(b['vendor_name'])}{recur}\n"
            f"   {_amount(b['amount'])} · {b['status']} · due {_due(b)}"
        )
    if any(b["status"] in EDITABLE_STATUSES for b in bills):
        lines.append("\n_Tap a bill below to edit or cancel it._")
    return "\n".join(lines)


def _fields_keyboard(bill_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏢 Vendor", callback_data=f"bill_field:{bill_id}:vendor"),
            InlineKeyboardButton("💰 Amount", callback_data=f"bill_field:{bill_id}:amount"),
        ],
        [
            InlineKeyboardButton("📅 Due date", callback_data=f"bill_field:{bill_id}:due"),
            InlineKeyboardButton("🏦 Account", callback_data=f"bill_field:{bill_id}:account"),
        ],
        [InlineKeyboardButton("🏛 Bank", callback_data=f"bill_field:{bill_id}:bank")],
        [InlineKeyboardButton("❌ Cancel bill", callback_data=f"bill_cancel:{bill_id}")],
        [InlineKeyboardButton("⬅️ Back to bills", callback_data="bill_menu")],
    ])


def _fields_text(bill: dict) -> str:
    repeat = bill["recurrence_interval"] if bill["is_recurring"] else "one-off"
    return (
        f"✏️ *Bill #{bill['id']}*\n\n"
        f"🏢 Vendor: *{escape_md(bill['vendor_name'])}*\n"
        f"💰 Amount: *{_amount(bill['amount'])}*\n"
        f"📅 Due: *{_due(bill)}*\n"
        f"🏦 Account: `{escape_md(bill['account_number'] or 'N/A')}`\n"
        f"🏛 Bank: {escape_md(bill['bank_name'] or bill['bank_code'] or 'N/A')}\n"
        f"🔁 Repeat: {repeat}\n"
        f"📌 Status: {bill['status']}\n\n"
        f"Tap a field to change it."
    )


def _value_keyboard(bill_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬅️ Back", callback_data=f"bill_field_back:{bill_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"bill_field_cancel:{bill_id}"),
        ]
    ])


def _prompt(bill: dict, field: str) -> str:
    if field == "vendor":
        return (
            f"🏢 *Vendor name*\nCurrent: `{escape_md(bill['vendor_name'])}`\n\n"
            "Send the new vendor name."
        )
    if field == "amount":
        return (
            f"💰 *Amount*\nCurrent: *{_amount(bill['amount'])}*\n\n"
            "Send the new amount (₦100 - ₦10,000,000)."
        )
    if field == "due":
        return (
            f"📅 *Due date*\nCurrent: *{_due(bill)}*\n\n"
            "Send the new date, e.g. `2026-12-31` or `31 Dec 2026`."
        )
    if field == "account":
        return (
            f"🏦 *Account number*\nCurrent: `{escape_md(bill['account_number'] or 'N/A')}`\n\n"
            "Send the 10 or 11 digit account number."
        )
    return (
        f"🏛 *Bank*\nCurrent: {escape_md(bill['bank_name'] or bill['bank_code'] or 'N/A')}\n\n"
        "Send the bank name or code. If several banks match I'll show the options."
    )


def _bank_options_keyboard(bill_id: int, matches: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{m.name[:30]} ({m.code})",
                callback_data=f"bill_bank:{bill_id}:{m.code}",
            )
        ]
        for m in matches
    ]
    rows.append(
        [InlineKeyboardButton("❌ Cancel", callback_data=f"bill_field_cancel:{bill_id}")]
    )
    return InlineKeyboardMarkup(rows)


# ── Bank resolution ────────────────────────────────────────────────


def _resolve_bank(text: str):
    """Return `(exact_match, candidates, error)`.

    A fuzzy candidate list is never auto-picked: a wrong bank code sends
    money to the wrong place.
    """
    from app.services.banks import get_bank_directory

    with session_scope() as session:
        directory = get_bank_directory()
        directory.ensure_loaded(session)
        exact = directory.resolve_exact(session, text)
        candidates = [] if exact is not None else directory.resolve_name(session, text)

    if exact is not None:
        return exact, [], None
    if candidates:
        return None, candidates, None
    return (
        None,
        [],
        f"I couldn't match *{escape_md(text)}* to a bank. "
        "Try the full name, or type the numeric code.",
    )


def _bank_name(code: str) -> str:
    from app.services.banks import get_bank_directory

    with session_scope() as session:
        directory = get_bank_directory()
        directory.ensure_loaded(session)
        bank = directory.get(session, code)
        return bank.name if bank is not None else code


# ── Persistence ────────────────────────────────────────────────────


def _apply_changes(user_id: int, bill_id: int, changes: dict):
    """Apply edits. Returns `(view, error, conversation_ended)`."""
    with session_scope() as session:
        bill = session.get(Bill, bill_id)
        if bill is None or bill.user_id != user_id:
            return None, f"Bill #{bill_id} not found.", True
        try:
            update_bill(session, bill=bill, changes=changes)
        except BillNotEditableError as exc:
            return None, str(exc), True
        except BillValidationError as exc:
            return None, str(exc), False
        return _view(bill), None, False


# ── /bills entry point ─────────────────────────────────────────────


async def bills_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = str(update.effective_chat.id)
    user = get_linked_user(chat_id)
    if user is None:
        await update.message.reply_text(
            "🔒 Link your account first with `/link YOUR_CODE`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data.pop("bm", None)
    bills = _load_bills(user.id)
    if not bills:
        await update.message.reply_text(
            "📭 You have no bills yet. Send me a bill, or `/schedule` a recurring one."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        _menu_text(bills),
        parse_mode="Markdown",
        reply_markup=_menu_keyboard(bills),
    )
    return ConversationHandler.END


# ── Bill selected ──────────────────────────────────────────────────


async def _callback_bill(query, *, action: str) -> tuple[User, dict] | None:
    """Answer a bill tap and return `(user, bill)`, or refuse in place.

    Every handler that acts on a bill id goes through here, so the
    ownership and editable-status rules live in one place.
    """
    await query.answer()
    bill_id = int(query.data.split(":")[1])

    user = get_linked_user(str(query.message.chat_id))
    if user is None:
        await safe_edit(query, "🔒 Link your account with /link first.")
        return None

    bill = _load_bill(user.id, bill_id)
    if bill is None:
        await safe_edit(query, f"Bill #{bill_id} not found.")
        return None
    if bill["status"] not in EDITABLE_STATUSES:
        await safe_edit(
            query,
            f"🔒 Bill #{bill_id} is *{bill['status']}* and can no longer be {action}.",
            parse_mode="Markdown",
        )
        return None
    return user, bill


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    guard = await _callback_bill(query, action="edited")
    if guard is None:
        return ConversationHandler.END
    _user, bill = guard
    bill_id = bill["id"]

    context.user_data["bm"] = {"bill_id": bill_id}
    await safe_edit(
        query,
        _fields_text(bill),
        parse_mode="Markdown",
        reply_markup=_fields_keyboard(bill_id),
    )
    return BILL_FIELDS


async def handle_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or parts[2] not in _FIELDS:
        await query.answer()
        await safe_edit(query, "⚠️ Unknown field. Run /bills again.")
        return ConversationHandler.END
    field = parts[2]

    guard = await _callback_bill(query, action="edited")
    if guard is None:
        return ConversationHandler.END
    _user, bill = guard
    bill_id = bill["id"]

    context.user_data["bm"] = {"bill_id": bill_id, "field": field}
    await safe_edit(
        query,
        _prompt(bill, field),
        parse_mode="Markdown",
        reply_markup=_value_keyboard(bill_id),
    )
    return BILL_VALUE


# ── Typed value ────────────────────────────────────────────────────


async def handle_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bm = context.user_data.get("bm") or {}
    bill_id = bm.get("bill_id")
    field = bm.get("field")
    if bill_id is None or field is None:
        await update.message.reply_text("Open a bill first with /bills.")
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text(
            "⚠️ Send a value, or tap Back.",
            reply_markup=_value_keyboard(bill_id),
        )
        return BILL_VALUE

    user = get_linked_user(str(update.effective_chat.id))
    if user is None:
        await update.message.reply_text("🔒 Link your account with /link first.")
        return ConversationHandler.END

    if field == "bank":
        exact, candidates, error = _resolve_bank(text)
        if error is not None:
            await update.message.reply_text(
                error,
                parse_mode="Markdown",
                reply_markup=_value_keyboard(bill_id),
            )
            return BILL_VALUE
        if candidates:
            await update.message.reply_text(
                f"Which bank did you mean by *{escape_md(text)}*?",
                parse_mode="Markdown",
                reply_markup=_bank_options_keyboard(bill_id, candidates),
            )
            return BILL_VALUE
        changes = {"bank_code": exact.code, "bank_name": exact.name}
    elif field == "vendor":
        changes = {"vendor_name": text}
    elif field == "amount":
        cleaned = text.replace(",", "").replace("₦", "").replace(" ", "")
        try:
            changes = {"amount": Decimal(cleaned)}
        except (InvalidOperation, ValueError):
            await update.message.reply_text(
                f"⚠️ *{escape_md(text)}* isn't a valid amount. Try again:",
                parse_mode="Markdown",
                reply_markup=_value_keyboard(bill_id),
            )
            return BILL_VALUE
    elif field == "due":
        parsed = parse_user_date(text)
        if parsed is None:
            await update.message.reply_text(
                f"⚠️ I couldn't parse *{escape_md(text)}* as a date. "
                "Try `2026-12-31` or `31 Dec 2026`:",
                parse_mode="Markdown",
                reply_markup=_value_keyboard(bill_id),
            )
            return BILL_VALUE
        changes = {"due_date": parsed}
    else:  # account
        changes = {"account_number": text}

    view, error, ended = _apply_changes(user.id, bill_id, changes)
    if error is not None:
        await update.message.reply_text(
            f"⚠️ {escape_md(error)}",
            parse_mode="Markdown",
            **({} if ended else {"reply_markup": _value_keyboard(bill_id)}),
        )
        return ConversationHandler.END if ended else BILL_VALUE

    await update.message.reply_text(
        "✅ Bill updated.\n\n" + _fields_text(view),
        parse_mode="Markdown",
        reply_markup=_fields_keyboard(bill_id),
    )
    return BILL_FIELDS


async def handle_bank_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, raw_id, code = query.data.split(":", 2)
    bill_id = int(raw_id)

    bm = context.user_data.get("bm") or {}
    if bm.get("bill_id") != bill_id or bm.get("field") != "bank":
        await safe_edit(query, "⚠️ That bank choice expired. Run /bills again.")
        return ConversationHandler.END

    user = get_linked_user(str(query.message.chat_id))
    if user is None:
        await safe_edit(query, "🔒 Link your account with /link first.")
        return ConversationHandler.END

    view, error, ended = _apply_changes(
        user.id, bill_id, {"bank_code": code, "bank_name": _bank_name(code)}
    )
    if error is not None:
        await safe_edit(query, f"⚠️ {escape_md(error)}", parse_mode="Markdown")
        return ConversationHandler.END if ended else BILL_VALUE

    await safe_edit(
        query,
        "✅ Bill updated.\n\n" + _fields_text(view),
        parse_mode="Markdown",
        reply_markup=_fields_keyboard(bill_id),
    )
    return BILL_FIELDS


# ── Back / close ──────────────────────────────────────────────────


async def handle_back_to_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("bm", None)

    user = get_linked_user(str(query.message.chat_id))
    if user is None:
        await safe_edit(query, "🔒 Link your account with /link first.")
        return ConversationHandler.END

    bills = _load_bills(user.id)
    if not bills:
        await safe_edit(query, "📭 You have no bills yet.")
        return ConversationHandler.END
    await safe_edit(
        query,
        _menu_text(bills),
        parse_mode="Markdown",
        reply_markup=_menu_keyboard(bills),
    )
    return ConversationHandler.END


async def handle_back_to_fields(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Back from a typed-value prompt, or "No" on the cancel prompt."""
    query = update.callback_query
    guard = await _callback_bill(query, action="edited")
    if guard is None:
        return ConversationHandler.END
    _user, bill = guard
    bill_id = bill["id"]

    context.user_data["bm"] = {"bill_id": bill_id}
    await safe_edit(
        query,
        _fields_text(bill),
        parse_mode="Markdown",
        reply_markup=_fields_keyboard(bill_id),
    )
    return BILL_FIELDS


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    with contextlib.suppress(Exception):
        await query.answer()
    context.user_data.pop("bm", None)
    await safe_edit(query, "❌ Closed. Run /bills again whenever you want.")
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("bm", None)
    await update.message.reply_text(
        "❌ Closed. Run /bills again whenever you want."
    )
    return ConversationHandler.END


# ── Cancel bill ────────────────────────────────────────────────────


async def handle_cancel_bill(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    guard = await _callback_bill(query, action="cancelled")
    if guard is None:
        return ConversationHandler.END
    _user, bill = guard
    bill_id = bill["id"]

    context.user_data["bm"] = {"bill_id": bill_id}
    await safe_edit(
        query,
        f"⚠️ *Cancel bill #{bill_id}?*\n\n"
        f"{escape_md(bill['vendor_name'])} — {_amount(bill['amount'])}\n\n"
        "Cancelling is permanent; you'd have to create a new bill.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Yes, cancel it", callback_data=f"bill_cancel_yes:{bill_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ No, go back", callback_data=f"bill_cancel_no:{bill_id}"
                )
            ],
        ]),
    )
    return BILL_FIELDS


async def handle_cancel_yes(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    bill_id = int(query.data.split(":")[1])

    user = get_linked_user(str(query.message.chat_id))
    if user is None:
        await safe_edit(query, "🔒 Link your account with /link first.")
        return ConversationHandler.END

    with session_scope() as session:
        bill = session.get(Bill, bill_id)
        if bill is None or bill.user_id != user.id:
            await safe_edit(query, f"Bill #{bill_id} not found.")
            return ConversationHandler.END
        try:
            cancel_bill(session, bill=bill)
        except BillNotEditableError as exc:
            await safe_edit(
                query, f"⚠️ {escape_md(str(exc))}", parse_mode="Markdown"
            )
            return ConversationHandler.END

    context.user_data.pop("bm", None)
    await safe_edit(query, f"❌ Bill #{bill_id} cancelled. Run /bills to see the rest.")
    return ConversationHandler.END


# ── ConversationHandler factory ────────────────────────────────────


def build_bill_management() -> ConversationHandler:
    edit_entry = CallbackQueryHandler(
        handle_edit, pattern=r"^bill_edit:\d+$"
    )
    field = CallbackQueryHandler(
        handle_field, pattern=r"^bill_field:\d+:(vendor|amount|due|account|bank)$"
    )
    bank_choice = CallbackQueryHandler(
        handle_bank_choice, pattern=r"^bill_bank:\d+:[A-Za-z0-9]+$"
    )
    back_fields = CallbackQueryHandler(
        handle_back_to_fields, pattern=r"^bill_field_back:\d+$"
    )
    cancel_no = CallbackQueryHandler(
        handle_back_to_fields, pattern=r"^bill_cancel_no:\d+$"
    )
    close = CallbackQueryHandler(
        handle_close, pattern=r"^bill_field_cancel:\d+$"
    )
    back_menu = CallbackQueryHandler(
        handle_back_to_menu, pattern=r"^bill_menu$"
    )
    cancel_bill = CallbackQueryHandler(
        handle_cancel_bill, pattern=r"^bill_cancel:\d+$"
    )
    cancel_yes = CallbackQueryHandler(
        handle_cancel_yes, pattern=r"^bill_cancel_yes:\d+$"
    )

    return ConversationHandler(
        entry_points=[CommandHandler("bills", bills_menu), edit_entry],
        states={
            BILL_FIELDS: [
                edit_entry,
                field,
                back_menu,
                cancel_bill,
                cancel_yes,
                cancel_no,
            ],
            BILL_VALUE: [
                edit_entry,
                field,
                back_menu,
                cancel_bill,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_value),
                bank_choice,
                back_fields,
                close,
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
    )


__all__ = ["build_bill_management"]
