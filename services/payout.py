import logging
import uuid
from datetime import datetime, timedelta
from sqlmodel import Session, select
from core.database import engine
from models.bill import Bill
from models.user import User
from models.transaction import Transaction
from .payaza import execute_payout
from telegram import Bot
import os
from services.banks import get_bank_code1


logger = logging.getLogger(__name__)
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

PAYAZA_FEE = 50.0  # Flat fee per transaction — update with real Payaza schedule


async def process_payout(bill_id: int) -> dict:
    """
    Core payout function. Used by both immediate and scheduled payments.
    Returns { success, message }
    """
    with Session(engine) as session:

        # ── 1. Load bill + user ────────────────────────────────────
        bill = session.get(Bill, bill_id)
        if not bill:
            return {"success": False, "message": "Bill not found"}

        user = session.get(User, bill.user_id)
        if not user:
            return {"success": False, "message": "User not found"}

        # ── 2. Atomic status check — prevent double processing ────_
        if bill.status == "processing":
            logger.warning(f"Bill {bill_id} already processing — skipping")
            return {"success": False, "message": "Already processing"}

        total_charge = bill.amount + PAYAZA_FEE

        # ── 3. Balance check ───────────────────────────────────────
        if user.balance < total_charge:
            shortfall = total_charge - user.balance
            await _notify_user(
                user.telegram_chat_id,
                f"⚠️ *Insufficient Balance*\n\n"
                f"Bill: {bill.vendor_name} — ₦{bill.amount:,.2f}\n"
                f"Fee: ₦{PAYAZA_FEE:,.2f}\n"
                f"Your balance: ₦{user.balance:,.2f}\n"
                f"Shortfall: ₦{shortfall:,.2f}\n\n"
                f"Top up your virtual account to proceed."
            )
            bill.status = "pending"
            session.add(bill)
            session.commit()
            return {"success": False, "message": "Insufficient balance"}

        # ── 4. Mark as processing (atomic) ─────────────────────────
        bill.status = "processing"
        session.add(bill)
        session.commit()

        # ── 5. Create transaction record ───────────────────────────
        reference = f"autopay_{bill_id}_{uuid.uuid4().hex[:8]}"
        transaction = Transaction(
            user_id=user.id,
            bill_id=bill.id,
            type="debit",
            amount=bill.amount,
            fee=PAYAZA_FEE,
            currency=bill.currency,
            status="processing",
            payaza_reference=reference,
            narration=f"Payment to {bill.vendor_name}",
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)

        bank_code = get_bank_code1(bill.bank_code)
        if not bank_code:
            raise ValueError(f"Invalid bank name: {bill.bank_code}")

        # ── 6. Call Payaza ─────────────────────────────────────────
        try:
            payaza_response = await execute_payout(
                transaction_reference=reference,
                amount=bill.amount,
                account_number=bill.account_number,
                bank_code=bank_code,
                account_name=bill.vendor_name,  # verified name stored at extraction
                narration=f"AutoPay: {bill.vendor_name}",
                sender_name=user.first_name,
                sender_address=user.address,
                sender_phone=user.phone_number
            )

            logger.info(f"Payaza response for bill {bill_id}: {payaza_response}")

            user.balance -= total_charge
            bill.status = "paid"
            transaction.status = "success"
            transaction.updated_at = datetime.utcnow()
            session.add(user)
            session.add(bill)
            session.add(transaction)
            session.commit()

            if bill.is_recurring:
                schedule_recurrence(bill, session)

            await _notify_user(
                user.telegram_chat_id,
                f"✅ *Payment Successful*\n\n"
                f"₦{bill.amount:,.2f} paid to *{bill.vendor_name}*\n"
                f"Reference: `{reference}`\n"
                f"Remaining balance: ₦{user.balance:,.2f}"
            )

            logger.info(f"Bill {bill_id} paid successfully — ref: {reference}")
            return {"success": True, "message": "Payment successful", "reference": reference}

        except Exception as e:
            # ── 8. Payaza rejected the request — refund reserved funds
            logger.error(f"Payout failed for bill {bill_id}: {e}")

            user.balance += total_charge   # refund
            bill.retry_count += 1
            transaction.status = "failed"
            transaction.failure_reason = str(e)
            transaction.updated_at = datetime.utcnow()

            if bill.retry_count >= bill.max_retries:
                bill.status = "failed"
                await _notify_user(
                    user.telegram_chat_id,
                    f"❌ *Payment Failed*\n\n"
                    f"Bill: {bill.vendor_name} — ₦{bill.amount:,.2f}\n"
                    f"After {bill.max_retries} attempts, payment could not be completed.\n"
                    f"Reason: {str(e)}\n\n"
                    f"Please check your details and try again."
                )
            else:
                bill.status = "scheduled"
                await _notify_user(
                    user.telegram_chat_id,
                    f"⚠️ *Payment attempt failed — will retry*\n\n"
                    f"Bill: {bill.vendor_name}\n"
                    f"Attempt {bill.retry_count}/{bill.max_retries}\n"
                    f"Reason: {str(e)}"
                )

            session.add(user)
            session.add(bill)
            session.add(transaction)
            session.commit()
            return {"success": False, "message": str(e)}



async def _notify_user(telegram_chat_id: str, message: str):
    """Fire-and-forget Telegram notification."""
    try:
        if telegram_chat_id:
            await bot.send_message(
                chat_id=telegram_chat_id,
                text=message,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Failed to notify user {telegram_chat_id}: {e}")


def schedule_recurrence(bill: Bill, session: Session):
    """Called after a successful recurring payment. Creates the next bill."""
    if not bill.is_recurring or not bill.recurrence_interval:
        return

    delta = timedelta(days=30 if bill.recurrence_interval == "monthly" else 7)
    next_due = bill.due_date + delta

    next_bill = Bill(
        user_id=bill.user_id,
        vendor_name=bill.vendor_name,
        amount=bill.amount,
        currency=bill.currency,
        due_date=next_due,
        account_number=bill.account_number,
        bank_code=bill.bank_code,
        status="scheduled",
        is_recurring=True,
        recurrence_interval=bill.recurrence_interval,
    )
    session.add(next_bill)
    session.commit()
    logger.info(f"Next recurrence for bill {bill.id} scheduled for {next_due}")
