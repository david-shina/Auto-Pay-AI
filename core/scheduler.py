import logging
from datetime import datetime, timedelta
from sqlmodel import Session, select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.database import engine
from app.models.bill import Bill
from app.services.payout import process_payout

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def process_scheduled_bills():
    """
    Runs on a schedule. Finds all bills that are due and processes them.
    """
    now = datetime.now()
    logger.info(f"[Scheduler] Running at {now.strftime('%Y-%m-%d %H:%M:%S')}")

    with Session(engine) as session:
        due_bills = session.exec(
            select(Bill).where(
                Bill.status == "scheduled",
                Bill.due_date <= now,          # due date has arrived
                Bill.retry_count < Bill.max_retries  # still has retries left
            )
        ).all()

    if not due_bills:
        logger.info("[Scheduler] No bills due — nothing to process")
        return

    logger.info(f"[Scheduler] Found {len(due_bills)} bill(s) to process")

    for bill in due_bills:
        logger.info(
            f"[Scheduler] Processing bill {bill.id} — "
            f"{bill.vendor_name} ₦{bill.amount:,.2f} due {bill.due_date}"
        )
        result = await process_payout(bill.id)

        if result["success"]:
            logger.info(f"[Scheduler] Bill {bill.id} paid — ref: {result.get('reference')}")
        else:
            logger.warning(f"[Scheduler] Bill {bill.id} failed — {result['message']}")


def start_scheduler():
    """Call this once at app startup."""
    scheduler.add_job(
        process_scheduled_bills,
        trigger="interval",
        minutes=1,           # checks every 30 minutes — adjust as needed
        id="scheduled_bills",
        replace_existing=True,
        next_run_time=datetime.now()  # run immediately on startup too
    )
    scheduler.start()
    logger.info("[Scheduler] Started — checking for due bills every 30 minutes")


def stop_scheduler():
    """Call this on app shutdown."""
    scheduler.shutdown()
    logger.info("[Scheduler] Stopped")
