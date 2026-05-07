import logging
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    filters,
)
from .agents.graphs import build_graph
from .services.loader import PDFLoader
from .services.imageloader import ImageLoader
from .services.textLoader import TextLoader
from helpers import (
    get_linked_user,
    format_bill_summary,
    confirm_keyboard,
    field_keyboard,
    EDITABLE_FIELDS,
)
from .services.payaza import verify_account_name, names_match
from .services.payout import process_payout, PAYAZA_FEE
from .models.bill import Bill
from .core.database import engine
from sqlmodel import Session
from dateutil import parser as dateparser
import os
from sqlmodel import Session, select
from .core.database import engine, get_session
from .models.user import User
from .agents.state import AgentState

logger = logging.getLogger(__name__)
agent = build_graph()
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Conversation states
CONFIRM, CHOOSE_FIELD, EDIT_VALUE, FINAL_CONFIRM, FINAL_CANCEL = range(5)


# ── Loader factory ─────────────────────────────────────────────────

def get_loader(file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.pdf':
        return PDFLoader(file_path)
    elif ext in ['.png', '.jpg', '.jpeg']:
        return ImageLoader(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ── Step 1: Receive bill ───────────────────────────────────────────

async def receive_bill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    chat_id = str(update.effective_chat.id)

    user = get_linked_user(chat_id)
    if not user:
        await msg.reply_text(
            "🔒 *Account not linked.*\n\nSend `/link YOUR_CODE` to connect your account.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    loader = None

    if msg.text and not msg.document and not msg.photo:
        loader = TextLoader(msg.text)

    elif msg.photo:
        photo_file_info = await msg.photo[-1].get_file()
        await photo_file_info.download_as_bytearray()
        loader = get_loader(photo_file_info.file_path)

    elif msg.document:
        if msg.document.mime_type == 'application/pdf':
            doc_file_info = await msg.document.get_file()
            await doc_file_info.download_as_bytearray()
            loader = get_loader(doc_file_info.file_path)
        else:
            await msg.reply_text("I can only process PDF documents.")
            return ConversationHandler.END
    else:
        return ConversationHandler.END

    await msg.reply_text("⏳ Extracting bill details...")

    try:
        extracted_data = await loader.extract_bill_info()
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        await msg.reply_text("❌ Couldn't extract bill details. Please try again.")
        return ConversationHandler.END

    context.user_data["bill"] = {
        "vendor_name": extracted_data.vendor_name,
        "amount": extracted_data.amount,
        "currency": extracted_data.currency,
        "due_date": extracted_data.due_date,
        "account_number": extracted_data.account_number,
        "bank_code": extracted_data.bank_code,
    }
    context.user_data["user_balance"] = user.balance
    context.user_data["user_currency"] = user.currency

    await msg.reply_text(
        format_bill_summary(context.user_data["bill"]),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard()
    )
    return CONFIRM


# ── Step 2a: Confirm → run agent ───────────────────────────────────

# async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     query = update.callback_query
#     await query.answer()

#     bill = context.user_data.get("bill", {})
#     await query.edit_message_text("🤖 Running agent analysis...")

#     try:
#         result = await agent.ainvoke({
#             'bill_id': 0,
#             'currency': context.user_data["user_currency"],
#             'user_balance': context.user_data["user_balance"],
#             'bill_amount': float(bill["amount"]),
#             'due_date': str(bill["due_date"]),
#             'decision': None,
#             'reasoning': None,
#             'is_approved': False
#         })

#         decision_emoji = {
#             "pay_now": "🔴",
#             "schedule": "🟡",
#             "hold": "⚪"
#         }.get(result["decision"], "🤖")

#         await query.edit_message_text(
#             f"✅ *Bill Confirmed*\n\n"
#             f"Vendor: {bill['vendor_name']}\n"
#             f"Amount: {float(bill['amount']):,.2f} {bill['currency']}\n"
#             f"Due: {bill['due_date']}\n\n"
#             f"{decision_emoji} *Decision:* {result['decision'].replace('_', ' ').title()}\n"
#             f"_{result['reasoning']}_",
#             parse_mode="Markdown"
#         )

#     except Exception as e:
#         logger.error(f"Agent failed: {e}")
#         await query.edit_message_text("❌ Agent analysis failed. Please try again.")

#     context.user_data.clear()
#     return ConversationHandler.END

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    bill_data = context.user_data.get("bill", {})

    await query.edit_message_text("🔍 Verifying account details...")

    if "user_id" not in context.user_data:
        telegram_chat_id = str(query.from_user.id)
        with Session(engine) as session:
            user = session.exec(
                select(User).where(User.telegram_chat_id == telegram_chat_id)
            ).first()

            if not user:
                await query.edit_message_text(
                    "❌ Your account was not found. Please use /start to link your account."
                )
                return ConversationHandler.END

            context.user_data["user_id"] = user.id
            context.user_data["user_balance"] = user.balance

    user_id = context.user_data["user_id"]
    user_balance = context.user_data["user_balance"]
    telegram_chat_id = str(query.from_user.id)

    # ── 1. Account name enquiry ────────────────────────────────────
    try:
        enquiry = await verify_account_name(
            
            account_number=bill_data["account_number"],
            bank_name = bill_data["bank_code"]
        )
        verified_name = enquiry.get("response_content", "")['account_name']
    except Exception as e:
        await query.edit_message_text(
            f"❌ Could not verify account details.\n"
            f"Error: {str(e)}\n\nPlease check the account number and try again."
        )
        return ConversationHandler.END

    # ── 2. Name match check ────────────────────────────────────────
    extracted_name = bill_data.get("vendor_name", "")
    match = names_match(extracted_name, verified_name, threshold=60)

    if not match:
        await query.edit_message_text(
            f"⚠️ *Account Name Mismatch*\n\n"
            f"Bill says: `{extracted_name}`\n"
            f"Bank says: `{verified_name}`\n\n"
            f"Payment has been blocked for your safety.\n"
            f"Please verify the account details and try again.",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        return ConversationHandler.END
    

    # ── 3. Save bill to DB ─────────────────────────────────────────
    with Session(engine) as session:
        db_bill = Bill(
            user_id=user_id,
            vendor_name=bill_data["vendor_name"],
            amount=float(bill_data["amount"]),
            currency=bill_data.get("currency", "NGN"),
            due_date=dateparser.parse(str(bill_data["due_date"])),
            account_number=bill_data["account_number"],
            bank_name=verified_name,   # store the verified name
            bank_code=bill_data["bank_code"],
            status="pending",
            is_recurring=context.user_data.get("is_recurring", False),
            recurrence_interval=context.user_data.get("recurrence_interval"),
        )
        session.add(db_bill)
        session.commit()
        session.refresh(db_bill)
        bill_id = db_bill.id
    

    await query.edit_message_text("🤖 Analysing payment...")

    # ── 4. Act on agent decision ───────────────────────────────────
    agent_input: AgentState = {
        "bill_id":          bill_id,
        "user_id":          user_id,
        "telegram_chat_id": telegram_chat_id,
        "currency":         bill_data.get("currency", "NGN"),
        "user_balance":     user_balance,
        "bill_amount":      float(bill_data["amount"]),
        "due_date":         str(bill_data["due_date"]),
        "decision":         None,
        "reasoning":        None,
        "is_approved":      False,
    }

    try:
        result = await agent.ainvoke(agent_input)
        decision = result["decision"]
        reasoning = result["reasoning"]
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        decision = "hold"
        reasoning = "Could not reach decision engine — payment held for safety."
    total = float(bill_data["amount"]) + PAYAZA_FEE

    if decision == "pay_now":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Payment", callback_data="final_confirm"),
                InlineKeyboardButton("❌ Cancel",          callback_data="final_cancel"),
            ]
        ])

        await query.edit_message_text(
            f"🤖 *Agent says: Pay Now*\n"
            f"_{reasoning}_\n\n"
            f"💳 *Payment Summary*\n"
            f"Vendor: *{bill_data['vendor_name']}*\n"
            f"Verified As: *{verified_name}*\n"
            f"Account: `{bill_data['account_number']}`\n\n"
            f"Amount:  ₦{float(bill_data['amount']):,.2f}\n"
            f"Fee:     ₦{PAYAZA_FEE:,.2f}\n"
            f"Total:   ₦{total:,.2f}\n"
            f"Balance after: ₦{user_balance - total:,.2f}\n\n"
            f"Do you want to proceed?",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        context.user_data["pending_bill_id"] = bill_id
        context.user_data["verified_name"]   = verified_name

        
        return FINAL_CONFIRM

    

    elif decision == "schedule":
        with Session(engine) as session:
            bill = session.get(Bill, bill_id)
            bill.status = "scheduled"
            session.add(bill)
            session.commit()

        await query.edit_message_text(
            f"🤖 *Agent says: Schedule*\n"
            f"_{reasoning}_\n\n"
            f"🗓 *Payment Scheduled*\n\n"
            f"₦{float(bill_data['amount']):,.2f} → {verified_name}\n"
            f"Due: {bill_data['due_date']}\n\n"
            f"I'll process it automatically when it's due.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END

    else:  # hold
        await query.edit_message_text(
            f"🤖 *Agent says: Hold*\n"
            f"_{reasoning}_\n\n"
            f"⏸ *Payment on Hold*\n\n"
            f"Bill: ₦{float(bill_data['amount']):,.2f} to {bill_data['vendor_name']}\n"
            f"Your Balance: ₦{user_balance:,.2f}\n"
            f"Shortfall: ₦{max(0, total - user_balance):,.2f}\n\n"
            f"Top up your wallet and send the bill again.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END


# ── Step 2b: Edit → show field picker ─────────────────────────────

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Which field would you like to correct?",
        reply_markup=field_keyboard()
    )
    return CHOOSE_FIELD


# ── Step 2c: Cancel ────────────────────────────────────────────────

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Bill cancelled. Send another bill whenever you're ready.")
    context.user_data.clear()
    return ConversationHandler.END


# ── Step 3: Field chosen → prompt for new value ────────────────────

async def handle_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        await query.edit_message_text(
            format_bill_summary(context.user_data["bill"]),
            parse_mode="Markdown",
            reply_markup=confirm_keyboard()
        )
        return CONFIRM

    field_key = query.data.replace("field:", "")
    context.user_data["editing_field"] = field_key
    current = context.user_data["bill"].get(field_key, "N/A")

    await query.edit_message_text(
        f"✏️ Editing *{EDITABLE_FIELDS[field_key]}*\n"
        f"Current value: `{current}`\n\n"
        f"Type the new value:",
        parse_mode="Markdown"
    )
    return EDIT_VALUE


# ── Step 4: New value typed → update and return to confirm ─────────

async def handle_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_value = update.message.text.strip()
    field_key = context.user_data.get("editing_field")
    field_label = EDITABLE_FIELDS.get(field_key, field_key)

    if field_key == "amount":
        try:
            new_value = float(new_value.replace(",", ""))
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid amount. Try again (e.g. `15000.00`):",
                parse_mode="Markdown"
            )
            return EDIT_VALUE

    context.user_data["bill"][field_key] = new_value

    await update.message.reply_text(
        f"✅ *{field_label}* updated to `{new_value}`\n\n"
        + format_bill_summary(context.user_data["bill"]),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard()
    )
    return CONFIRM

async def handle_final_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    bill_data     = context.user_data.get("bill", {})
    bill_id       = context.user_data["pending_bill_id"]
    verified_name = context.user_data["verified_name"]
    user_balance  = context.user_data["user_balance"]
    total         = float(bill_data["amount"]) + PAYAZA_FEE

    if query.data == "payment_cancelled":
        with Session(engine) as session:
            bill = session.get(Bill, bill_id)
            if bill:
                session.delete(bill)
                session.commit()

        await query.edit_message_text(
            "🚫 *Payment Cancelled*\n\n"
            "No money was moved. Send another bill whenever you're ready.",
            parse_mode="Markdown"
        )
        context.user_data.clear()
        return ConversationHandler.END

    # ── User confirmed — call process_payout ──────────────────────
    await query.edit_message_text("⏳ Processing payment...")

    result = await process_payout(bill_id)

    if result["success"]:
        await query.edit_message_text(
            f"✅ *Payment Successful*\n\n"
            f"₦{float(bill_data['amount']):,.2f} → *{verified_name}*\n"
            f"Reference: `{result.get('reference', 'N/A')}`\n"
            f"Remaining balance: ₦{user_balance - total:,.2f}",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"❌ *Payment Failed*\n\n"
            f"Reason: {result['message']}\n\n"
            f"Please try again or top up your wallet.",
            parse_mode="Markdown"
        )

    context.user_data.clear()
    return ConversationHandler.END


# ── Cancel command fallback ────────────────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send a new bill whenever you're ready.")
    return ConversationHandler.END


# ── Assemble the ConversationHandler ──────────────────────────────

def build_bill_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT | filters.PHOTO | filters.Document.ALL,
                receive_bill
            )
        ],
        states={
            CONFIRM: [
                CallbackQueryHandler(handle_confirm, pattern="^confirm$"),
                CallbackQueryHandler(handle_edit, pattern="^edit$"),
                CallbackQueryHandler(handle_cancel, pattern="^cancel$"),
            ],
            CHOOSE_FIELD: [
                CallbackQueryHandler(handle_field_choice, pattern="^(field:.+|back)$"),
            ],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_value)
            ],

            # ── Add this ───────────────────────────────────────────
            FINAL_CONFIRM: [
                CallbackQueryHandler(handle_final_confirm, pattern="^final_confirm$"),
                CallbackQueryHandler(handle_cancel, pattern="^final_cancel$"),
            ],

        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
    )
