"""Payout service — execute a bill payment via the configured provider.

The race condition in the MVP at `payout.py:38-40` (two concurrent
requests both pass the "is not processing" check before either
commits the flip) is fixed here with `SELECT ... FOR UPDATE` so the
two transactions serialize at the database layer.

Key invariants this module guarantees:
  1. A bill is in exactly one terminal state at a time. The status
     transition is atomic with the balance change and the audit row.
  2. The balance is never negative. The `SELECT FOR UPDATE` + early
     check guards the wallet.
  3. The provider call happens *after* the row is locked and marked
     `processing`, so a duplicate web-hook for the same `reference`
     cannot double-debit the user.
  4. If the provider call fails after the wallet debit, the refund +
     audit row are written in the same transaction. There is no window
     where the user has been charged but the system "forgot."
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.models.bill import Bill
from app.models.enums import AuditActor, BillStatus, TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.services.audit import (
    audit_payout_failed,
    audit_payout_succeeded,
    audit_wallet_debit,
)
from app.services.name_match import names_match
from app.services.payments import (
    AccountNameMismatchError,
    InsufficientFundsError,
    InvalidAccountError,
    PaymentError,
    PaymentProvider,
)

# Note: `notify_refund` is imported lazily inside `_refund_on_failure`
# to avoid a circular import. `app.services.telegram` imports the
# bot handlers (which import `execute_payout` from this module) at
# module-load time.

logger = logging.getLogger(__name__)


# ── Result type ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PayoutResult:
    success: bool
    message: str
    reference: str
    new_balance: Decimal
    # True when the provider accepted the transfer but is holding it for
    # OTP approval (`status == "otp"`). The wallet has been debited and
    # the bill is `processing`; the caller must surface this so the
    # operator can submit the OTP via finalize_pending_transfer().
    requires_otp: bool = False


# ── Helpers ─────────────────────────────────────────────────────────

def _new_reference(bill_id: int) -> str:
    """Our outgoing transfer reference (idempotency key at the provider)."""
    return f"autopay_{bill_id}_{uuid.uuid4().hex[:12]}"


def _ngn_to_kobo(amount: Decimal) -> int:
    """₦ → kobo (integer). Paystack wants whole kobo only."""
    return int((amount * Decimal(100)).quantize(Decimal("1")))


# ── Core entry point ────────────────────────────────────────────────

async def execute_payout(
    session: Session,
    *,
    bill_id: int,
    provider: PaymentProvider,
    actor: AuditActor = AuditActor.USER,
) -> PayoutResult:
    """Process a payout for `bill_id`.

    Must be called from inside a `with session.begin():` block (or
    wherever the caller wants the transaction boundary). The
    `SELECT ... FOR UPDATE` is on `bills` AND `users` (via
    separate row-locks in the same transaction) to serialize
    concurrent attempts.

    The provider call is awaited. If it raises a typed `PaymentError`,
    we translate it into a refund + audit row. If it raises anything
    else, we re-raise (the caller can decide whether to roll back the
    transaction — typically yes, to keep the bill in `processing` for
    a retry).
    """
    # ── 1. Lock the bill row ───────────────────────────────────────
    bill = session.execute(
        select(Bill).where(Bill.id == bill_id).with_for_update()
    ).scalar_one_or_none()
    if bill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bill {bill_id} not found.",
        )

    # ── 2. Lock the user row (separate FOR UPDATE) ────────────────
    user = session.execute(
        select(User).where(User.id == bill.user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {bill.user_id} not found.",
        )

    # ── 3. State checks ────────────────────────────────────────────
    if bill.status == BillStatus.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is already paid.",
        )
    if bill.status == BillStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is cancelled.",
        )
    if bill.status == BillStatus.PROCESSING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is already being processed.",
        )

    fee = Decimal(str(settings.payout_fee_ngn))
    total_charge = Decimal(str(bill.amount)) + fee

    # ── 4. Balance check ───────────────────────────────────────────
    if Decimal(str(user.balance)) < total_charge:
        shortfall = total_charge - Decimal(str(user.balance))
        bill.status = BillStatus.PENDING.value
        session.add(bill)
        audit_payout_failed(
            session,
            user_id=user.id,
            bill_id=bill.id,
            reason=f"insufficient_balance (shortfall={shortfall})",
            retry_count=bill.retry_count,
            actor=actor,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Insufficient balance. Need {total_charge} NGN, "
                f"have {user.balance} NGN (shortfall {shortfall} NGN)."
            ),
        )

    # ── 5. Mark processing + create debit transaction (atomic) ────
    bill.status = BillStatus.PROCESSING.value
    reference = _new_reference(bill.id)

    debit = Transaction(
        user_id=user.id,
        bill_id=bill.id,
        type=TransactionType.DEBIT.value,
        amount=bill.amount,
        fee=fee,
        currency=bill.currency,
        status=TransactionStatus.PROCESSING.value,
        provider=provider.name,
        provider_reference=reference,
        narration=f"Payment to {bill.vendor_name}",
    )
    session.add(debit)
    session.flush()  # populate debit.id for the audit row

    audit_wallet_debit(
        session,
        user_id=user.id,
        amount=float(bill.amount),
        fee=float(fee),
        bill_id=bill.id,
        provider_reference=reference,
        new_balance=float(user.balance) - float(total_charge),  # pre-debit balance; real update follows
        actor=actor,
    )

    # ── 6. Resolve account + create transfer recipient ─────────────
    if not bill.account_number or not bill.bank_code:
        unresolved_bank = bool(bill.bank_name) and not bill.bank_code
        await _refund_on_failure(
            session, user, bill, debit,
            (
                f"bank_not_resolved: {bill.bank_name!r}"
                if unresolved_bank
                else "missing account_number or bank_code"
            ),
            actor=actor,
        )
        _commit_or_warn(session)
        if unresolved_bank:
            detail = (
                f"This bill's bank ({bill.bank_name!r}) has not been matched to a "
                "bank code yet. Choose the bank and update the bill before paying."
            )
        elif not bill.account_number:
            detail = "Bill has no payout account number configured."
        else:
            detail = "Bill has no payout account configured."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    try:
        resolved = await provider.resolve_account(
            account_number=bill.account_number, bank_code=bill.bank_code
        )
    except InvalidAccountError as exc:
        # Don't leak the raw provider message (e.g. "Invalid key",
        # "Account not found at bank 058") to the user. The full
        # detail goes to logs/audit; the user gets a clean message.
        logger.warning("Account resolution failed for bill %d: %s", bill.id, exc)
        await _refund_on_failure(
            session, user, bill, debit, f"invalid_account: {exc}", actor=actor,
        )
        _commit_or_warn(session)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not verify the bank account. Please check the account number and bank.",
        ) from exc

    # If we have a stored account name, verify it matches the resolved
    # one (defense against typos / account swaps). A mismatch means
    # the user typed the wrong vendor for this account, or the account
    # at this number now belongs to a different entity. Either way
    # we should not transfer the user's money — refund + 422.
    if bill.vendor_name and not names_match(bill.vendor_name, resolved.account_name):
        await _refund_on_failure(
            session,
            user,
            bill,
            debit,
            f"name_mismatch: bill={bill.vendor_name!r} resolved={resolved.account_name!r}",
            actor=actor,
        )
        _commit_or_warn(session)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Account name mismatch: the bill is for {bill.vendor_name!r} "
                f"but account {bill.account_number} at bank {bill.bank_code} "
                f"is registered to {resolved.account_name!r}. "
                f"Please edit the bill vendor or check the account number."
            ),
        )

    try:
        recipient_code = await provider.create_transfer_recipient(
            account_number=bill.account_number,
            bank_code=bill.bank_code,
            account_name=resolved.account_name,
        )
    except AccountNameMismatchError as exc:
        # The provider returned a name that doesn't match the one we
        # already verified upstream. This is a defense-in-depth check;
        # the user gets the same friendly mismatch message as the
        # primary check above.
        logger.warning("Provider-reported name mismatch for bill %d: %s", bill.id, exc)
        await _refund_on_failure(
            session, user, bill, debit, f"name_mismatch: {exc}", actor=actor,
        )
        _commit_or_warn(session)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Account name mismatch. The account holder's name on "
                "file does not match this vendor. Please verify the "
                "details and try again."
            ),
        ) from exc
    except PaymentError as exc:
        await _refund_on_failure(
            session, user, bill, debit, f"recipient_failed: {exc}", actor=actor,
        )
        _commit_or_warn(session)
        raise

    # ── 7. Initiate the transfer ───────────────────────────────────
    # The provider's response matters for one case: `status == "otp"`.
    # Paystack then accepted the transfer but is holding it for OTP
    # approval and the money has NOT moved. We keep going (the wallet is
    # debited, the bill stays `processing`) but record the
    # `transfer_code` so the transfer can be finalized, or refunded once
    # the OTP window expires.
    try:
        transfer = await provider.initiate_transfer(
            amount_kobo=_ngn_to_kobo(Decimal(str(bill.amount))),
            recipient_code=recipient_code,
            reference=reference,
            reason=f"AutoPay: {bill.vendor_name}",
        )
    except InsufficientFundsError as exc:
        # Provider says our MERCHANT balance is too low. Refund user.
        await _refund_on_failure(
            session, user, bill, debit,
            f"provider_insufficient_funds: {exc}", actor=actor,
        )
        _commit_or_warn(session)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider temporarily out of funds. Please retry later.",
        ) from exc
    except PaymentError as exc:
        await _refund_on_failure(
            session, user, bill, debit, f"transfer_failed: {exc}", actor=actor,
        )
        _commit_or_warn(session)
        raise

    # ── 8. Commit wallet + mark as processing (success) ───────────
    requires_otp = transfer.status == "otp"
    if requires_otp and not transfer.transfer_code:
        # No transfer_code means we could never call finalize_transfer,
        # so the money would sit debited with no way to release it.
        # Refund now instead.
        await _refund_on_failure(
            session, user, bill, debit,
            "otp_requested_without_transfer_code", actor=actor,
        )
        _commit_or_warn(session)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The payment provider asked for an OTP but returned no "
                "transfer code. Please try again."
            ),
        )

    user.balance = Decimal(str(user.balance)) - total_charge
    debit.status = TransactionStatus.PROCESSING.value  # remains 'processing' until webhook/finalize confirms
    if requires_otp:
        debit.provider_transfer_code = transfer.transfer_code
    session.add(user)
    session.add(debit)
    session.flush()

    # Note: the bill stays in 'processing' until the transfer is
    # confirmed - by the transfer.success/failed webhook, by an explicit
    # OTP finalize, or by the reconciliation job polling the provider.
    if requires_otp:
        logger.info(
            "payout.otp_required bill_id=%d reference=%s transfer_code=%s",
            bill.id, reference, transfer.transfer_code,
        )
        return PayoutResult(
            success=True,
            message=(
                "Transfer is waiting for OTP approval. Submit the OTP Paystack "
                "sent you to complete the payment."
            ),
            reference=reference,
            new_balance=Decimal(str(user.balance)),
            requires_otp=True,
        )

    return PayoutResult(
        success=True,
        message="Transfer initiated; awaiting provider confirmation.",
        reference=reference,
        new_balance=Decimal(str(user.balance)),
    )


# ── Refund helper ───────────────────────────────────────────────────

def _commit_or_warn(session: Session) -> None:
    """Commit the current transaction and swallow commit errors as
    warnings. Used by the failure branches of `execute_payout` so the
    audit row + bill status changes persist even when we raise an
    HTTPException immediately after. Callers (FastAPI endpoint, bot
    handler, scheduler) can roll back their own outer transaction
    safely — the audit is already durable."""
    try:
        session.commit()
    except Exception as exc:
        logger.warning("commit after refund failed (audit may be lost): %s", exc)


async def _refund_on_failure(
    session: Session,
    user: User,
    bill: Bill,
    debit: Transaction,
    reason: str,
    actor: AuditActor = AuditActor.USER,
) -> None:
    """Mark the debit failed, increment retry, set bill back to
    scheduled-or-failed. The user's balance is *not* debited in this
    path because the wallet-debit audit row + transaction row are
    also being marked failed; the actual wallet balance was never
    touched (the transfer row is the only place we wrote the
    reservation)."""
    debit.status = TransactionStatus.FAILED.value
    debit.failure_reason = reason
    bill.retry_count += 1
    if bill.retry_count >= bill.max_retries:
        bill.status = BillStatus.FAILED.value
    else:
        bill.status = BillStatus.SCHEDULED.value
    session.add(debit)
    session.add(bill)
    session.flush()
    audit_payout_failed(
        session,
        user_id=user.id,
        bill_id=bill.id,
        reason=reason,
        actor=actor,
        retry_count=bill.retry_count,
    )

    # Best-effort Telegram notification of the refund. notify_refund
    # swallows its own errors so the payout flow continues even if
    # the bot is offline or the user isn't linked. Imported lazily
    # to avoid a circular import.
    from app.services.telegram import notify_refund
    await notify_refund(user=user, transaction=debit)


# ── Recurrence helper (used after a successful payout) ──────────────

def schedule_recurrence(session: Session, *, bill: Bill) -> Bill | None:
    """Create the next bill in a recurring series. Returns it, or None
    if the bill is not recurring. Caller is responsible for committing.

    Also updates the original bill's `next_recurrence_date` so the
    scheduler doesn't re-process the same bill on the next run.
    """
    if not bill.is_recurring or not bill.recurrence_interval:
        return None

    delta = timedelta(days=30 if bill.recurrence_interval == "monthly" else 7)
    next_due = bill.due_date + delta

    # Bump the original's next_recurrence_date so the scheduler skips
    # it next time. Strip tzinfo to match the column type.
    nrd = next_due
    if nrd.tzinfo is not None:
        nrd = nrd.replace(tzinfo=None)
    bill.next_recurrence_date = nrd
    session.add(bill)

    next_bill = Bill(
        user_id=bill.user_id,
        vendor_name=bill.vendor_name,
        amount=bill.amount,
        currency=bill.currency,
        due_date=next_due,
        account_number=bill.account_number,
        bank_code=bill.bank_code,
        bank_name=bill.bank_name,
        status=BillStatus.SCHEDULED.value,
        is_recurring=True,
        recurrence_interval=bill.recurrence_interval,
        next_recurrence_date=nrd,
    )
    session.add(next_bill)
    session.flush()
    # The scheduler is the current caller of this recurrence helper.
    # Bump the metric so recurring bills are visible in
    # `app_bills_created_total{trigger="recurring"}`.
    from app.core.metrics import record_bill_created

    record_bill_created(trigger="recurring")
    return next_bill


# ── OTP finalization (operator-submitted) ──────────────────────────

async def finalize_pending_transfer(
    session: Session,
    *,
    user: User,
    otp: str,
    provider: PaymentProvider,
    bill_id: int | None = None,
) -> PayoutResult:
    """Complete a payout the provider parked awaiting OTP approval.

    Finds the OTP-pending debit (for `bill_id` when given, otherwise the
    user's most recent one), submits the OTP, then applies a terminal
    outcome through `confirm_payout` so success/failure bookkeeping and
    any refund follow exactly the same path as the webhook.

    A rejected OTP does not refund: the operator can retry with a fresh
    code, and the reconciliation job refunds once the OTP window expires.
    """
    # Bind the user to this session: callers (the bot) hand in a User
    # loaded by a different, already-closed session, so its `balance`
    # would be stale when we build the result.
    db_user = session.get(User, user.id) or user

    stmt = select(Transaction).where(
        Transaction.user_id == db_user.id,
        Transaction.type == TransactionType.DEBIT.value,
        Transaction.status == TransactionStatus.PROCESSING.value,
        Transaction.provider_transfer_code.is_not(None),
    )
    if bill_id is not None:
        stmt = stmt.where(Transaction.bill_id == bill_id)
    stmt = stmt.order_by(Transaction.created_at.desc()).limit(1).with_for_update()

    txn = session.exec(stmt).first()
    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No transfer is waiting for an OTP"
                + (f" for bill {bill_id}" if bill_id is not None else "")
                + "."
            ),
        )

    reference = txn.provider_reference or ""
    try:
        result = await provider.finalize_transfer(
            transfer_code=txn.provider_transfer_code or "",
            otp=otp.strip(),
        )
    except PaymentError as exc:
        logger.warning(
            "finalize_transfer rejected for reference %s: %s", reference, exc
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "That OTP was rejected or has expired. Request a new one "
                "and try again."
            ),
        ) from exc

    if result.status in ("pending", "queued", "otp"):
        # Accepted but not settled yet: stay in processing and let the
        # webhook or the reconciliation job settle it.
        session.commit()
        return PayoutResult(
            success=True,
            message="OTP accepted. Transfer is still being processed.",
            reference=reference,
            new_balance=Decimal(str(db_user.balance)),
        )

    outcome = confirm_payout(
        session,
        provider_reference=reference,
        success=result.status == "success",
        failure_reason=None if result.status == "success" else f"otp_{result.status}",
    )
    session.commit()
    session.refresh(db_user)

    if outcome is None:  # pragma: no cover - txn was found above
        return PayoutResult(
            success=False,
            message="Transfer not found for this reference.",
            reference=reference,
            new_balance=Decimal(str(db_user.balance)),
        )
    return outcome


# ── Webhook-side confirmation (called from webhook handler) ────────

def confirm_payout(
    session: Session,
    *,
    provider_reference: str,
    success: bool,
    failure_reason: str | None = None,
) -> PayoutResult | None:
    """Apply the final state transition when the provider webhook
    confirms the transfer. Returns the payout result for the caller
    to log/respond, or None if no matching transaction is found (a
    webhook for something we never initiated — log and ignore).

    Idempotent: called again with the same outcome is a no-op.
    """
    txn = session.exec(
        select(Transaction).where(Transaction.provider_reference == provider_reference)
    ).first()
    if txn is None:
        logger.warning(
            "Webhook for unknown provider_reference=%s — ignoring", provider_reference
        )
        return None
    if txn.bill_id is None:
        return None

    bill = session.get(Bill, txn.bill_id)
    if bill is None:  # pragma: no cover
        return None

    # Idempotency: if the transaction is already in a terminal state,
    # don't change anything.
    if txn.status in (TransactionStatus.SUCCESS.value, TransactionStatus.FAILED.value):
        return PayoutResult(
            success=txn.status == TransactionStatus.SUCCESS.value,
            message="Already reconciled.",
            reference=provider_reference,
            new_balance=Decimal("0"),
        )

    user = session.get(User, txn.user_id)
    if user is None:  # pragma: no cover
        return None

    if success:
        txn.status = TransactionStatus.SUCCESS.value
        bill.status = BillStatus.PAID.value
        audit_payout_succeeded(
            session,
            user_id=user.id,
            bill_id=bill.id,
            provider_reference=provider_reference,
        )
        # Metrics: bill paid via webhook-side confirmation. The
        # scheduler's auto-pay path already emits its own record;
        # this covers the manual / provider-direct success path.
        from app.core.metrics import record_bill_paid, record_payout

        record_bill_paid(trigger="auto_pay")
        record_payout(result="success")
        message = "Payment successful."
    else:
        txn.status = TransactionStatus.FAILED.value
        txn.failure_reason = failure_reason or "provider_rejected"
        # Refund the wallet: revert the balance that was debited in
        # execute_payout. Fee is also refunded (we never actually
        # paid it).
        user.balance = Decimal(str(user.balance)) + txn.amount + txn.fee
        bill.retry_count += 1
        bill.status = (
            BillStatus.FAILED.value
            if bill.retry_count >= bill.max_retries
            else BillStatus.SCHEDULED.value
        )
        session.add(user)
        audit_payout_failed(
            session,
            user_id=user.id,
            bill_id=bill.id,
            reason=failure_reason or "transfer_failed",
            retry_count=bill.retry_count,
        )
        # Metrics: payout failed at the provider side.
        from app.core.metrics import record_payout

        record_payout(result="provider_error")
        message = f"Payment failed: {failure_reason or 'unknown reason'}."

    session.add(txn)
    session.add(bill)
    session.flush()

    return PayoutResult(
        success=success,
        message=message,
        reference=provider_reference,
        new_balance=Decimal(str(user.balance)),
    )
