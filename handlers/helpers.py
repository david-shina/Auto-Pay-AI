from sqlmodel import Session, select
from core.database import engine
from models.user import User
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

EDITABLE_FIELDS = {
    "vendor_name": "Vendor name",
    "amount": "Amount",
    "due_date": "Due date",
    "account_number": "Account number",
    "bank_code": "Bank code",
}

def get_linked_user(chat_id: str) -> User | None:
    with Session(engine) as session:
        return session.exec(
            select(User).where(
                User.telegram_chat_id == chat_id,
                User.is_telegram_linked == True
            )
        ).first()


def format_bill_summary(data: dict) -> str:
    return (
        f"📄 *Extracted Bill Details*\n\n"
        f"🏢 Vendor: `{data.get('vendor_name', 'N/A')}`\n"
        f"💰 Amount: `{float(data.get('amount', 0)):,.2f} {data.get('currency', 'NGN')}`\n"
        f"📅 Due date: `{data.get('due_date', 'N/A')}`\n"
        f"🏦 Account: `{data.get('account_number', 'N/A')}`\n"
        f"🔢 Bank code: `{data.get('bank_code', 'N/A')}`\n\n"
        f"Is this correct?"
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="edit"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])


def field_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"field:{key}")]
        for key, label in EDITABLE_FIELDS.items()
    ]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    return InlineKeyboardMarkup(buttons)
