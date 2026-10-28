"""Bills API — upload, list, get, pay, cancel.

Mounted at /api/v1/bills in `app.main`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlmodel import Session, select

from app.agents.graphs import run_agent
from app.agents.state import Decision
from app.core.config import settings
from app.core.database import get_session
from app.core.metrics import record_bill_created
from app.models.bill import Bill
from app.models.enums import BillStatus
from app.models.user import User
from app.schemas.bill import (
    BillActionResponse,
    BillBankUpdateRequest,
    BillCreateRequest,
    BillResponse,
    TransferOtpRequest,
)
from app.services.audit import audit_bill_created
from app.services.auth import get_current_active_user
from app.services.banks import get_bank_directory
from app.services.date_parser import parse_bill_due_date
from app.services.loaders import loader_from_upload
from app.services.payments import PaymentProvider, get_payment_provider
from app.services.payout import execute_payout, finalize_pending_transfer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bills"])


def _resolve_bank_or_422(
    session: Session,
    *,
    bank_code: str | None,
    bank_name: str | None,
) -> tuple[str | None, str | None]:
    """Turn a supplied bank code-or-name into `(code, name)`.

    Accepts either a code ("058") or a name ("GTBank"). Raises 422 when
    the value matches more than one bank, because guessing can send the
    money to the wrong institution. The message names the top candidates
    so a non-SPA client can still recover; the SPA resolves through
    `/api/v1/banks/resolve` and shows a picker before submitting.
    """
    raw = (bank_code or "").strip() or (bank_name or "").strip()
    if not raw:
        return (None, None)

    directory = get_bank_directory()
    directory.ensure_loaded(session)

    exact = directory.resolve_exact(session, raw)
    if exact is not None:
        return (exact.code, exact.name)

    matches = directory.resolve_name(session, raw)
    detail = f"{raw!r} is not a recognised bank."
    if matches:
        hint = ", ".join(f"{m.name} ({m.code})" for m in matches[:5])
        detail += f" Did you mean: {hint}?"
    detail += " Search with GET /api/v1/banks?q= and send the chosen code as bank_code."
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=detail,
    )


def _normalize_bank_best_effort(
    session: Session,
    *,
    bank_code: str | None,
    bank_name: str | None,
) -> tuple[str | None, str | None]:
    """Like `_resolve_bank_or_422` but never raises.

    Used on the upload path, where the bank is often absent or garbled
    by OCR/LLM extraction. The bill is still created; an unresolvable
    bank leaves `bank_code=None` so the invalid value can never reach a
    transfer, and the user is prompted to pick the bank before paying.
    """
    directory = get_bank_directory()
    directory.ensure_loaded(session)
    return directory.normalize_bank_fields(
        session, code=bank_code, name=bank_name
    )


# ── POST /bills/upload ──────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=BillActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_bill(
    request_bill: str | None = Form(
        default=None,
        description="Raw bill text (alternative to uploading a file)",
    ),
    file: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    """Upload a bill (PDF / image) OR paste text, get back an
    extracted + agent-decided bill in the database."""
    if not file and not request_bill:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either a file upload or a 'request_bill' text field.",
        )

    # 1. Extract bill info
    if file is not None:
        data = await file.read()
        loader = loader_from_upload(
            filename=file.filename or "",
            content_type=file.content_type,
            data=data,
        )
    else:
        loader = loader_from_upload(
            filename="text.txt",
            content_type="text/plain",
            data=(request_bill or "").encode("utf-8"),
        )

    try:
        extracted = await loader.extract()
    except Exception as exc:
        logger.exception("Bill extraction failed for user %d: %s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read the bill. Try a clearer photo or paste the text.",
        ) from exc

    if not extracted.vendor_name or float(extracted.amount) <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Could not determine vendor and amount. "
                "Provide 'request_bill' text or upload a clearer file."
            ),
        )

    # 2. Create the bill
    bank_code, bank_name = _normalize_bank_best_effort(
        session, bank_code=extracted.bank_code, bank_name=extracted.bank_name
    )
    bill = Bill(
        user_id=user.id,
        vendor_name=extracted.vendor_name,
        amount=Decimal(str(extracted.amount)),
        currency=extracted.currency or "NGN",
        due_date=parse_bill_due_date(extracted.due_date),
        account_number=extracted.account_number,
        bank_code=bank_code,
        bank_name=bank_name,
        status=BillStatus.PENDING.value,
    )
    session.add(bill)
    session.flush()
    audit_bill_created(
        session,
        user_id=user.id,
        bill_id=bill.id,
        amount=float(bill.amount),
        provider=provider,
    )

    # 3. Run the decision agent
    days_until_due = (bill.due_date - datetime.now()).days
    decision = run_agent(
        user_balance=Decimal(str(user.balance)),
        bill_amount=Decimal(str(bill.amount)),
        fee=Decimal(str(settings.payout_fee_ngn)),
        days_until_due=days_until_due,
    )

    # 4. If agent says pay_now, run the payout
    message = "Bill created."
    if decision.decision == Decision.PAY_NOW:
        try:
            result = await execute_payout(session, bill_id=bill.id, provider=provider)
            session.commit()
            message = result.message
            from app.core.metrics import record_bill_paid, record_payout
            record_bill_paid(trigger="manual")
            record_payout(result="success")
        except HTTPException:
            session.rollback()
            # Payout refused (insufficient funds, etc). Bill is still
            # in the DB; the user can /pay it again later.
            message = "Bill created; payout deferred (see decision reason)."
    elif decision.decision == Decision.SCHEDULE:
        bill.status = BillStatus.SCHEDULED.value
        session.add(bill)
        message = "Bill scheduled for later evaluation."

    session.commit()
    session.refresh(bill)
    record_bill_created(trigger="upload")

    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message=message,
        decision=decision.decision.value,
        decision_reason=decision.reason,
    )


# ── POST /bills (no upload) ─────────────────────────────────────────

@router.post(
    "",
    response_model=BillActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bill(
    payload: BillCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    bank_code, bank_name = _resolve_bank_or_422(
        session, bank_code=payload.bank_code, bank_name=payload.bank_name
    )
    bill = Bill(
        user_id=user.id,
        vendor_name=payload.vendor_name,
        amount=Decimal(str(payload.amount)),
        currency="NGN",
        due_date=payload.due_date,
        account_number=payload.account_number,
        bank_code=bank_code,
        bank_name=bank_name,
        status=BillStatus.PENDING.value,
    )
    session.add(bill)
    session.flush()
    audit_bill_created(
        session,
        user_id=user.id,
        bill_id=bill.id,
        amount=float(bill.amount),
        provider=provider.name,
    )
    session.commit()
    session.refresh(bill)
    record_bill_created(trigger="manual")
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message="Bill created.",
    )


@router.patch("/{bill_id}", response_model=BillResponse)
def update_bill_payout_account(
    bill_id: int,
    payload: BillBankUpdateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillResponse:
    """Correct the payout destination (bank / account) on a bill.

    This is the escape hatch for bills created before the bank
    directory existed, or whose bank came back from OCR as a name we
    could not resolve. Without it the only fix was to delete and
    recreate the bill.
    """
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status in (BillStatus.PAID.value, BillStatus.PROCESSING.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This bill has already been paid or is being paid.",
        )

    if payload.account_number is not None:
        bill.account_number = payload.account_number.strip() or None

    if payload.bank_code is not None or payload.bank_name is not None:
        bank_code, bank_name = _resolve_bank_or_422(
            session, bank_code=payload.bank_code, bank_name=payload.bank_name
        )
        bill.bank_code = bank_code
        bill.bank_name = bank_name

    session.add(bill)
    session.commit()
    session.refresh(bill)
    return BillResponse.model_validate(bill)


# ── GET /bills ──────────────────────────────────────────────────────
@router.get("", response_model=list[BillResponse])
def list_bills(
    status_filter: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> list[BillResponse]:
    q = select(Bill).where(Bill.user_id == user.id)
    if status_filter:
        q = q.where(Bill.status == status_filter)
    # `id` breaks ties: several bills commonly share a due date, and
    # without a tiebreaker the DB returns equal-date rows in arbitrary
    # order.
    q = q.order_by(Bill.due_date.asc(), Bill.id.asc())
    rows = session.exec(q).all()
    return [BillResponse.model_validate(r) for r in rows]


# ── GET /bills/{id} ─────────────────────────────────────────────────

@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")
    return BillResponse.model_validate(bill)


# ── POST /bills/{id}/pay ────────────────────────────────────────────

@router.post("/{bill_id}/pay", response_model=BillActionResponse)
async def pay_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")

    try:
        result = await execute_payout(session, bill_id=bill.id, provider=provider)
        session.commit()
        from app.core.metrics import record_bill_paid, record_payout
        record_bill_paid(trigger="manual")
        record_payout(result="success")
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        logger.exception("Manual bill payout failed for bill %d: %s", bill_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment provider error. Please try again later.",
        )

    bill = session.get(Bill, bill.id)  # refresh
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message=result.message,
        decision=(
            "otp_required" if result.requires_otp else Decision.PAY_NOW.value
        ),
        decision_reason=(
            "The transfer needs OTP approval. POST the OTP to "
            "/api/v1/bills/{id}/transfer-otp to finish it."
            if result.requires_otp
            else None
        ),
    )


# ── POST /bills/{id}/transfer-otp ───────────────────────────────────

@router.post("/{bill_id}/transfer-otp", response_model=BillActionResponse)
async def submit_transfer_otp(
    bill_id: int,
    payload: TransferOtpRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    """Finish a transfer the provider is holding for OTP approval.

    Paystack sends the OTP to the merchant's registered phone/email, so
    this is an operator action. Scoped to the bill's owner.
    """
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")

    result = await finalize_pending_transfer(
        session,
        user=user,
        otp=payload.otp,
        provider=provider,
        bill_id=bill.id,
    )
    session.refresh(bill)
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message=result.message,
    )


# ── POST /bills/{id}/cancel ─────────────────────────────────────────

@router.post("/{bill_id}/cancel", response_model=BillActionResponse)
def cancel_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillActionResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status in (BillStatus.PAID.value, BillStatus.PROCESSING.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a bill in status '{bill.status}'.",
        )
    bill.status = BillStatus.CANCELLED.value
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message="Bill cancelled.",
    )
