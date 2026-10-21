"""Webhook handlers.

Mounted at /webhooks/paystack in `app.main`.

Paystack POSTs raw JSON bodies; we MUST verify the per-provider
signature header BEFORE parsing JSON, to prevent attackers from
forging `charge.success` events.

REPLAY DEFENSE: We dedup on (provider, event_id) via the
`webhook_events` table. Paystack retries the same event on a network
blip; the second delivery is a 200 no-op with an audit breadcrumb.

The actual credit / debit logic is now in `app.services.wallet` and
`app.services.payout` — the routes below are thin dispatchers.

(The previous Nomba webhook support has been moved to the standalone
`nomba-gateway` sibling project.)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.enums import AuditActor, AuditEntityType, AuditEventType
from app.models.transaction import Transaction
from app.models.user import User
from app.models.webhook_event import WebhookEvent as WebhookEventRow
from app.services.audit import write_audit
from app.services.payments import (
    PaymentProvider,
    WebhookSignatureError,
    get_payment_provider,
)
from app.services.payout import confirm_payout
from app.services.telegram import notify_debit, notify_refund
from app.services.wallet import (
    apply_credit_from_provider_event,
    credit_virtual_account_deposit,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook(
    request: Request,
    session: Session = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> dict:
    """Receive + verify + dispatch a Paystack webhook event."""
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature") or ""

    try:
        event = await provider.parse_webhook(
            raw_body=raw_body, signature_header=signature
        )
    except WebhookSignatureError as exc:
        logger.warning("Paystack webhook with bad signature: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        ) from exc

    # Replay defense: the (provider, event_id) UNIQUE constraint on
    # webhook_events makes a second delivery of the same event a 200
    # no-op. Race-safe via the UNIQUE constraint + IntegrityError.
    try:
        session.add(
            WebhookEventRow(
                provider=provider.name,
                event_id=event.event_id,
                event_type=event.event_type,
            )
        )
        session.flush()
    except IntegrityError:
        session.rollback()
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WEBHOOK_REPLAY,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "provider": provider.name,
                "event_id": event.event_id,
                "event_type": event.event_type,
            },
        )
        session.commit()
        logger.info("Paystack webhook replay rejected: %s", event.event_id)
        return {"received": True, "replay": True, "event": event.event_type}

    logger.info(
        "Paystack webhook: event=%s reference=%s amount_kobo=%s",
        event.event_type, event.provider_reference, event.amount_kobo,
    )

    if event.event_type == "charge.success":
        await _handle_charge_success(session, event)
    elif event.event_type in ("transfer.success", "transfer.failed", "transfer.reversed"):
        await _handle_transfer_update(session, event)
    elif event.event_type == "dedicatedaccount.assign.success":
        _handle_dva_assigned(session, event)
    else:
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WEBHOOK_UNKNOWN,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "provider": provider.name,
                "event_type": event.event_type,
                "reference": event.provider_reference,
                "event_id": event.event_id,
            },
        )
        session.commit()

    return {"received": True, "event": event.event_type}


# ── Handlers ────────────────────────────────────────────────────────

async def _handle_charge_success(session: Session, event) -> None:
    """User's VA received money. Credit their wallet and update txn.

    The actual credit is delegated to
    `app.services.wallet.apply_credit_from_provider_event` so the
    same logic is shared by the webhook + manual-verify +
    scheduler-poll paths. That function uses
    `SELECT ... FOR UPDATE` to make concurrent credits of the
    same transaction safe.
    """
    if not event.provider_reference:
        logger.warning("charge.success with no reference: %s", event.raw)
        return

    # Resolve the user BEFORE the row lock so we have `user_id` to
    # pass into the credit function (the function needs the User
    # object to update `balance` + fire the Telegram notification).
    txn = session.exec(
        select(Transaction).where(
            Transaction.provider_reference == event.provider_reference
        )
    ).first()
    if txn is None:
        # No matching transaction. The money may still be a transfer
        # into the user's dedicated virtual account, which Paystack
        # reports with the gateway's customer code rather than one of
        # our references. Try that path before giving up.
        result = await credit_virtual_account_deposit(session, event=event)
        if result is not None and result.credited:
            return

        # Still nothing — let the shared function log the orphan
        # credit + return early. We pass a synthetic user; the function's
        # user_id check will trip and write the orphan_credit audit row.
        from app.models.user import User as _User

        synthetic = _User(
            id=0, email="", hashed_password="",
            first_name="", last_name="",
            phone_number="", balance=0,
            currency="NGN",
        )
        await apply_credit_from_provider_event(
            session, user=synthetic, event=event,
        )
        return

    user = session.get(User, txn.user_id)
    if user is None:
        logger.warning(
            "charge.success for reference %s: user %d not found",
            event.provider_reference, txn.user_id,
        )
        return

    await apply_credit_from_provider_event(
        session, user=user, event=event,
    )


async def _handle_transfer_update(session: Session, event) -> None:
    """Our outbound transfer completed / failed / was reversed."""
    success = event.event_type == "transfer.success"
    failure_reason: str | None = None
    if not success:
        failure_reason = event.event_type  # "transfer.failed" | "transfer.reversed"

    payout_result = confirm_payout(
        session,
        provider_reference=event.provider_reference,
        success=success,
        failure_reason=failure_reason,
    )
    session.commit()

    # Best-effort Telegram notification. We only fire this if the
    # payout actually changed state (confirm_payout returns None
    # for no-ops like unknown references or already-reconciled
    # transactions). Refresh user + txn so the notifier sees the
    # new balance.
    if payout_result is not None and payout_result.success is True:
        # Look up the now-reconciled txn so we can hand it to the
        # notifier (which formats amount / narration / fee from it).
        from sqlalchemy import select as _sa_select

        from app.models.transaction import Transaction

        txn = session.execute(
            _sa_select(Transaction).where(
                Transaction.provider_reference == event.provider_reference
            )
        ).scalar_one_or_none()
        if txn is not None:
            user = session.get(User, txn.user_id)
            if user is not None:
                await notify_debit(user=user, transaction=txn)
    elif payout_result is not None and payout_result.success is False:
        # Refund path: the user's balance was credited back.
        from sqlalchemy import select as _sa_select

        from app.models.transaction import Transaction

        txn = session.execute(
            _sa_select(Transaction).where(
                Transaction.provider_reference == event.provider_reference
            )
        ).scalar_one_or_none()
        if txn is not None:
            user = session.get(User, txn.user_id)
            if user is not None:
                await notify_refund(user=user, transaction=txn)


def _handle_dva_assigned(session: Session, event) -> None:
    """DVA was successfully assigned. Signup already created the row;
    this is mostly an audit-log breadcrumb."""
    write_audit(
        session,
        actor=AuditActor.WEBHOOK,
        event_type=AuditEventType.VA_CREATED,
        user_id=None,
        entity_type=AuditEntityType.VIRTUAL_ACCOUNT,
        entity_id=None,
        metadata={"event": "dva_assigned", "reference": event.provider_reference, "event_id": event.event_id},
    )
    session.commit()
