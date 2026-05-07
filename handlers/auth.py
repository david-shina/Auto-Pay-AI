import httpx
import logging
from telegram import Update
from telegram.ext import ContextTypes
from .helpers import get_linked_user
from ..models.transaction import VirtualAccount
from sqlmodel import Session, select
from ..core.database import engine

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *AutoPay AI!*\n\n"
        "To get started, link your web account:\n"
        "1. Sign up at the web dashboard\n"
        "2. Go to Settings → Link Telegram\n"
        "3. Send me: `/link YOUR_CODE`\n\n"
        "Once linked, just send me a bill photo, PDF, or text!",
        parse_mode="Markdown"
    )


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text(
            "Please include your linking code.\n"
            "Example: `/link AB9X2K`\n\n"
            "Get your code from the web dashboard.",
            parse_mode="Markdown"
        )
        return

    code = args[0].strip().upper()
    chat_id = str(update.effective_chat.id)

    # Check if already linked
    user = get_linked_user(chat_id)
    if user:
        await update.message.reply_text(
            "✅ Your account is already linked! Just send me a bill."
        )
        return

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/auth/verify-link",
            json={"code": code, "telegram_chat_id": chat_id}
        )

    if response.status_code == 200:
        await update.message.reply_text(
            "✅ *Account linked successfully!*\n\n"
            "You can now send me bills (photos, PDFs, or text) "
            "and I'll analyze and schedule payments automatically.",
            parse_mode="Markdown"
        )
        return

    try:
        raw_detail = response.json().get("detail", "Unknown error")
        detail = raw_detail if isinstance(raw_detail, str) else str(raw_detail)
    except Exception:
        detail = f"HTTP {response.status_code}"

    error_messages = {
        "Code expired": "⏰ That code has expired. Generate a new one from the web dashboard.",
        "Code already used": "⚠️ That code has already been used. Generate a new one if needed.",
        "Invalid code": "❌ That code is invalid. Please check and try again.",
        "Telegram already linked to another account": "⚠️ This Telegram is already linked to a different account.",
    }
    await update.message.reply_text(
        error_messages.get(detail, f"❌ Linking failed: {detail}")
    )


# handlers/auth.py

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wallet — shows balance and virtual account details."""
    chat_id = str(update.effective_chat.id)
    user = get_linked_user(chat_id)

    if not user:
        await update.message.reply_text(
            "🔒 Link your account first with `/link YOUR_CODE`",
            parse_mode="Markdown"
        )
        return

    with Session(engine) as session:
        va = session.exec(
            select(VirtualAccount).where(VirtualAccount.user_id == user.id)
        ).first()

    if not va:
        await update.message.reply_text(
            "⚠️ No virtual account found. Please contact support."
        )
        return

    await update.message.reply_text(
        f"💼 *Your AutoPay Wallet*\n\n"
        f"Balance: ₦{user.balance:,.2f}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"*Fund your wallet via transfer:*\n"
        f"Bank: `{va.bank_name}`\n"
        f"Account: `{va.account_number}`\n"
        f"Name: `{va.account_name}`\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"_Save this as a beneficiary in your bank app for quick top-ups._",
        parse_mode="Markdown"
    )