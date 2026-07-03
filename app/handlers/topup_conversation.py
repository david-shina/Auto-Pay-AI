"""Bot /topup conversation — hosted Checkout top-up.

The user can top up their wallet from inside the bot without having
to log into the dashboard. The flow mirrors the bill-conversation
multi-field editor pattern (quick-pick buttons + typed-custom-input
fallback), but it's a single-step process so the state machine is
short.

Flow:
  1. User runs `/topup` (or taps a button in `/start` / `/help`).
  2. Bot shows quick-pick amounts (₦1k / 5k / 10k / 50k) + a
     "Custom" button + "Cancel".
  3. Tap a quick amount → mint reference, call the top-up service,
     reply with the Paystack authorization URL + a "Done" button.
  4. Tap "Custom" → ask the user to type an amount; validate
     (100 ≤ amount ≤ 1,000,000 NGN, parseable number) and either
     accept and continue or bounce back with a clear error.
  5. `/cancel` (or the "Cancel" button) ends the conversation
     without doing anything.

State machine:
  TOPUP_PICK   — showing the quick-pick keyboard
  TOPUP_CUSTOM — user is typing a custom amount

The actual top-up logic is delegated to `app.services.wallet.start_topup`
so the rules (min/max, audit, metrics) are identical to the API path.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

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
from app.handlers.helpers import escape_md, get_linked_user
from app.services import payments as _payments_pkg
from app.services.payments import PaymentError, get_payment_provider
from app.services.wallet import (
    MAX_TOPUP_NGN,
    MIN_TOPUP_NGN,
    TopupValidationError,
    start_topup,
)

logger = logging.getLogger(__name__)


# Conversation states
#   TOPUP_PICK    — showing the quick-pick keyboard
#   TOPUP_CUSTOM  — user is typing a custom amount
#   TOPUP_DONE    — URL has been delivered; user picks a follow-up
TOPUP_PICK, TOPUP_CUSTOM, TOPUP_DONE = range(3)


# ── Keyboards ──────────────────────────────────────────────────────


# Quick-pick amounts. 1k/5k/10k/50k are the most common bills;
# anything else the user can type as a custom amount.
QUICK_PICK_AMOUNTS: list[int] = [1000, 5000, 10_000, 50_000]


def quick_pick_keyboard() -> InlineKeyboardMarkup:
    """A 2-column grid of quick-pick amounts + Custom + Cancel."""
    rows: list[list[InlineKeyboardButton]] = []
    # Pair the amounts into rows of 2.
    for i in range(0, len(QUICK_PICK_AMOUNTS), 2):
        chunk = QUICK_PICK_AMOUNTS[i : i + 2]
        rows.append(
            [
                InlineKeyboardButton(
                    f"₦{amt:,}",
                    callback_data=f"topup_amount:{amt}",
                )
                for amt in chunk
            ]
        )
    rows.append(
        [InlineKeyboardButton("✏️ Custom amount", callback_data="topup_custom")]
    )
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="topup_cancel")])
    return InlineKeyboardMarkup(rows)


def done_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after a top-up URL is delivered. Instead of
    a dead-end Done button, give the user something to do next:
    top up again, check their balance, or close out the menu. The
    webhook will credit the wallet asynchronously; the user can
    come back later to /transactions to see the new row."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💸 Top up again", callback_data="topup_done_again"
            ),
            InlineKeyboardButton(
                "💼 Check balance", callback_data="topup_done_balance"
            ),
        ],
        [InlineKeyboardButton("✅ Done", callback_data="topup_done_close")],
    ])


# ── Step 1: /topup entry point ─────────────────────────────────────


async def topup_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User runs `/topup` → show the quick-pick keyboard."""
    chat_id = str(update.effective_chat.id)
    user = get_linked_user(chat_id)
    if user is None:
        await update.message.reply_text(
            "🔒 *Account not linked.*\n\n"
            "Send `/link YOUR_CODE` to connect your account, then "
            "try `/topup` again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "💸 *Top up your wallet*\n\n"
        f"Choose an amount, or tap *Custom* to type your own.\n"
        f"_Range: ₦{int(MIN_TOPUP_NGN):,} – ₦{int(MAX_TOPUP_NGN):,}_",
        parse_mode="Markdown",
        reply_markup=quick_pick_keyboard(),
    )
    return TOPUP_PICK


# ── Step 2a: user picked a quick amount ───────────────────────────


async def handle_quickpick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped a quick-pick amount button. Mint the top-up."""
    query = update.callback_query
    await query.answer()

    # Parse the amount out of the callback_data. Defensive — if
    # it's malformed, end the conversation.
    try:
        amount = int(query.data.replace("topup_amount:", ""))
    except (ValueError, AttributeError):
        await query.edit_message_text(
            "⚠️ Couldn't read that amount. Run /topup again."
        )
        return ConversationHandler.END

    return await _init_topup(
        query=query,
        context=context,
        amount=Decimal(amount),
        edit=True,
    )


# ── Step 2b: user picked "Custom" → ask for input ────────────────


async def handle_custom(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped the Custom button → ask them to type an amount."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ *Custom top-up amount*\n\n"
        f"Type the amount in NGN (e.g. `7500` or `12,500.00`).\n"
        f"_Range: ₦{int(MIN_TOPUP_NGN):,} – ₦{int(MAX_TOPUP_NGN):,}_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="topup_back")],
            [InlineKeyboardButton("❌ Cancel", callback_data="topup_cancel")],
        ]),
    )
    return TOPUP_CUSTOM


async def handle_custom_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped Back from the custom prompt → re-show quick-pick."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💸 *Top up your wallet*\n\n"
        "Choose an amount, or tap *Custom* to type your own.",
        parse_mode="Markdown",
        reply_markup=quick_pick_keyboard(),
    )
    return TOPUP_PICK


async def handle_custom_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User typed a custom amount. Validate, then start the top-up."""
    text = (update.message.text or "").strip()
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            f"⚠️ *{escape_md(text)}* isn't a valid amount.\n\n"
            f"Try again (e.g. `7500` or `12,500.00`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="topup_cancel")],
            ]),
        )
        return TOPUP_CUSTOM
    if amount < MIN_TOPUP_NGN or amount > MAX_TOPUP_NGN:
        await update.message.reply_text(
            f"⚠️ Amount must be between ₦{int(MIN_TOPUP_NGN):,} "
            f"and ₦{int(MAX_TOPUP_NGN):,}.\n\nTry again:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="topup_cancel")],
            ]),
        )
        return TOPUP_CUSTOM

    # Mint the top-up. The handler replies to a message (not a
    # callback query), so we pass a tiny shim that supports the
    # two methods our `_init_topup` helper uses.
    class _MsgShim:
        def __init__(self, msg):
            self._msg = msg

        async def answer(self) -> None:  # not used in this path
            pass

        async def edit_message_text(self, text, **kwargs):
            # We can't edit a message that we haven't edited before,
            # so just reply with a fresh message. The Done button
            # still works because the user is now in a terminal
            # state.
            await self._msg.reply_text(text, **kwargs)

    await _init_topup(
        query=_MsgShim(update.message),
        context=context,
        amount=amount,
        edit=False,
    )
    # Stay in the conversation at TOPUP_DONE so the follow-up
    # buttons can fire when the user taps them on the new message.
    return TOPUP_DONE


# ── Step 3: cancel / done ────────────────────────────────────────


async def handle_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped Cancel or sent /cancel mid-flow."""
    query = getattr(update, "callback_query", None) or update
    if hasattr(query, "answer"):
        try:
            await query.answer()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(query, "edit_message_text"):
        await query.edit_message_text(
            "❌ Top-up cancelled. Run /topup whenever you're ready."
        )
    elif hasattr(query, "message") and hasattr(query.message, "reply_text"):
        await query.message.reply_text(
            "❌ Top-up cancelled. Run /topup whenever you're ready."
        )
    return ConversationHandler.END


async def handle_done_again(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped "Top up again" — restart the flow from the
    quick-pick keyboard."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💸 *Top up your wallet*\n\n"
        "Choose an amount, or tap *Custom* to type your own.",
        parse_mode="Markdown",
        reply_markup=quick_pick_keyboard(),
    )
    return TOPUP_PICK


async def handle_done_balance(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped "Check balance" — show their current balance
    and end the conversation."""
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    user = get_linked_user(chat_id)
    if user is None:
        # Should never happen mid-conversation, but defend anyway.
        await query.edit_message_text(
            "🔒 Account not linked. Run /link first."
        )
        return ConversationHandler.END
    balance = float(user.balance)
    await query.edit_message_text(
        f"💼 *Your wallet balance*\n\n"
        f"₦{balance:,.2f}\n\n"
        f"_Your wallet will be credited automatically when Paystack "
        f"confirms the top-up. Run /transactions to see the new row._",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def handle_done_close(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User tapped the final "Done" — just acknowledge and end."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👍 *All set.*\n\n"
        "Your wallet will be credited automatically as soon as "
        "Paystack confirms the top-up. Run /transactions to see "
        "the new row appear.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /cancel command fallback (matches bill conversation) ─────────


async def cancel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await update.message.reply_text(
        "❌ Top-up cancelled. Run /topup whenever you're ready."
    )
    return ConversationHandler.END


# ── Shared helper: mint the top-up, deliver the URL ──────────────


async def _init_topup(
    *,
    query,
    context: ContextTypes.DEFAULT_TYPE,
    amount: Decimal,
    edit: bool,
) -> int:
    """Common path for both quick-pick and custom-amount top-ups.

    Resolves the user, calls `start_topup` (the same service the
    API uses), then shows the Paystack-hosted authorization URL.
    All audit + metrics + persistence rules apply identically.
    """
    chat_id = str(query._msg.chat_id if hasattr(query, "_msg") else "")  # type: ignore[attr-defined]
    # `query` in the quick-pick path is the real CallbackQuery; in
    # the custom-amount path it's a `_MsgShim`. We resolve the chat
    # id differently for each.
    if hasattr(query, "_msg"):
        chat_id = str(query._msg.chat_id)  # type: ignore[attr-defined]
    else:
        # Real CallbackQuery: get the chat from the message it
        # belongs to.
        chat_id = str(query.message.chat_id)

    user = get_linked_user(chat_id)
    if user is None:
        # The user got unlinked mid-flow. Tell them and end.
        await _reply(query, edit, "🔒 Account not linked. Run /link first.")
        return ConversationHandler.END

    # Resolve the provider through the package module so the
    # stub_provider fixture (which monkeypatches
    # `app.services.payments.get_payment_provider`) takes effect.
    provider = _payments_pkg.get_payment_provider()
    try:
        with session_scope() as session:
            result = await start_topup(
                session,
                user=user,
                amount=amount,
                provider=provider,
            )
    except TopupValidationError as exc:
        await _reply(query, edit, f"⚠️ {exc}\n\nRun /topup again.")
        return ConversationHandler.END
    except PaymentError as exc:
        await _reply(
            query, edit,
            f"❌ Couldn't start the top-up: {escape_md(str(exc))}\n\n"
            f"Try again in a moment, or contact support if it keeps failing.",
        )
        return ConversationHandler.END

    # The Paystack Checkout URL is meant to be opened in a browser.
    # Telegram doesn't render URLs inside inline keyboards cleanly,
    # so we display it as a clickable link in the message body and
    # also offer a "Open Checkout" button. The Done button just
    # acknowledges the user has seen the URL.
    text = (
        "💸 *Top-up ready*\n\n"
        f"Amount: *₦{float(result.amount):,.2f}*\n"
        f"Reference: `{escape_md(result.reference)}`\n\n"
        f"Open this link in your browser to pay:\n"
        f"{result.authorization_url}\n\n"
        f"✅ Your wallet will be credited automatically when Paystack "
        f"confirms. You can close this chat — we'll notify you when it lands."
    )
    await _reply(query, edit, text, reply_markup=done_keyboard())
    # Stay in the conversation at TOPUP_DONE so the follow-up
    # handlers (handle_done_again / handle_done_balance /
    # handle_done_close) can fire. When the user finally picks one,
    # those handlers return ConversationHandler.END (or TOPUP_PICK
    # for "Top up again").
    return TOPUP_DONE


async def _reply(query, edit: bool, text: str, **kwargs) -> None:
    """Send `text` either as an edit of the current message (if the
    user came from a callback query) or as a new reply (if they
    came from a typed message in the custom-amount flow)."""
    if edit and hasattr(query, "edit_message_text"):
        await query.edit_message_text(text, **kwargs)
    else:
        target = getattr(query, "_msg", None) or getattr(query, "message", None)
        if target is not None and hasattr(target, "reply_text"):
            await target.reply_text(text, **kwargs)


# ── ConversationHandler factory ───────────────────────────────────


def build_topup_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("topup", topup_command)],
        states={
            TOPUP_PICK: [
                CallbackQueryHandler(
                    handle_quickpick, pattern=r"^topup_amount:\d+$"
                ),
                CallbackQueryHandler(handle_custom, pattern=r"^topup_custom$"),
                CallbackQueryHandler(handle_cancel, pattern=r"^topup_cancel$"),
            ],
            TOPUP_CUSTOM: [
                # User is typing a custom amount.
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, handle_custom_amount
                ),
                CallbackQueryHandler(handle_custom_back, pattern=r"^topup_back$"),
                CallbackQueryHandler(handle_cancel, pattern=r"^topup_cancel$"),
            ],
            TOPUP_DONE: [
                # URL has been delivered; user picks a follow-up.
                CallbackQueryHandler(handle_done_again, pattern=r"^topup_done_again$"),
                CallbackQueryHandler(handle_done_balance, pattern=r"^topup_done_balance$"),
                CallbackQueryHandler(handle_done_close, pattern=r"^topup_done_close$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
    )
