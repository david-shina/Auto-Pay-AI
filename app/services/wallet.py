"""Wallet business logic — the operations that modify a user's balance.

Currently exposes one operation (`start_topup`) which both the FastAPI
endpoint at `POST /api/v1/wallet/topup` and the Telegram bot's
`/topup` conversation call. Centralizing here means the validation,
audit, and metrics rules are enforced in exactly one place.

Why a service instead of inline in the API route:
  * The bot needs the same logic, and re-implementing it in the
    ConversationHandler would duplicate (and drift on) the rules.
  * The two callers (API + bot) have different error UX (JSON 4xx vs
    Markdown error message), but the underlying operations are
    identical: validate amount, mint reference, persist pending txn,
    call provider, audit, metrics.

Also exposes `apply_credit_from_provider_event` — the credit path
shared by the webhook route and the manual-verify / scheduler-poll
fallback paths. Hoisted here so all three (webhook + verify + poll)
share the same idempotency guarantees, audit row format, and
race-condition protections.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.core.metrics import record_topup_credited, record_topup_initiated
from app.models.enums import (
    AuditActor,
    AuditEntityType,
    AuditEventType,
    TransactionStatus,
    TransactionType,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.services.audit import audit_wallet_credit, write_audit
from app.services.payments import (
    PaymentError,
    PaymentProvider,
    TopupInit,
    TransactionStatusResult,
    WebhookEvent,
)
# `notify_credit` is imported lazily inside the credit function to
# avoid a circular import: telegram.py → topup_conversation.py →
# wallet.py (the bot handler imports `MAX_TOPUP_NGN` etc. from
# here). The same pattern is used in `app/services/payout.py`.

logger = logging.getLogger(__name__)


# Top-up limits, shared with the API endpoint. Keep these here so the
# bot and the API can't drift apart.
MIN_TOPUP_NGN: Decimal = Decimal("100.00")
MAX_TOPUP_NGN: Decimal = Decimal("1_000_000.00")


class TopupValidationError(ValueError):
    """Raised when the top-up amount is outside the [min, max] range
    or otherwise unusable. Distinct from `PaymentError` (which means
    the provider failed) so callers can render a different message."""


@dataclass(frozen=True)
class TopupStartResult:
    """Return value of `start_topup` — what the bot/API needs to
    hand off to Paystack and the user."""

    authorization_url: str
    reference: str
    transaction_id: int
    amount: Decimal
    currency: str = "NGN"
    provider: str = "paystack"


@dataclass(frozen=True)
class CreditApplyResult:
    """Outcome of `apply_credit_from_provider_event`.

    `credited` is True iff this call actually flipped the transaction
    to SUCCESS and increased the user's balance. False for "already
    applied" (the webhook + poll + verify all share this) and for
    "unknown reference" (orphan credit).
    """

    credited: bool
    new_balance: Decimal
    status: str  # "credited" | "already_credited" | "unknown_reference"
    transaction_id: Optional[int] = None


async def apply_credit_from_provider_event(
    session: Session,
    *,
    user: User,
    event: WebhookEvent,
) -> CreditApplyResult:
    """Credit a user's wallet when a `charge.success` event arrives
    from Paystack. Used by the Paystack webhook route and reused by
    any other path that needs to apply a provider-driven credit.

    Row-locks the pending transaction (`SELECT ... FOR UPDATE`) and
    guards on `status == SUCCESS` to make concurrent calls
    idempotent. The Telegram notification is best-effort.
    """
    if not event.provider_reference:
        logger.warning("charge.success with no reference: %s", event.raw)
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="unknown_reference",
        )

    txn = session.execute(
        select(Transaction)
        .where(Transaction.provider_reference == event.provider_reference)
        .with_for_update()
    ).scalar_one_or_none()
    if txn is None:
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WALLET_CREDITED,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "reference": event.provider_reference,
                "amount_kobo": event.amount_kobo,
                "status": "orphan_credit",
            },
        )
        session.commit()
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="unknown_reference",
        )

    if txn.status == TransactionStatus.SUCCESS.value:
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="already_credited",
            transaction_id=txn.id,
        )

    if txn.user_id != user.id:
        logger.warning(
            "credit event for reference %s has user_id mismatch: "
            "txn.user_id=%d, event.user_id=%d",
            event.provider_reference, txn.user_id, user.id,
        )
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="unknown_reference",
        )

    amount = Decimal(str(event.amount_kobo or 0)) / Decimal(100)
    user.balance = Decimal(str(user.balance)) + amount
    txn.status = TransactionStatus.SUCCESS.value
    session.add(user)
    session.add(txn)
    audit_wallet_credit(
        session,
        user_id=user.id,
        amount=float(amount),
        provider_reference=event.provider_reference,
        new_balance=float(user.balance),
    )
    if event.provider_reference.startswith("topup_"):
        record_topup_credited(source="checkout")
    else:
        record_topup_credited(source="dva")
    session.commit()
    session.refresh(user)
    session.refresh(txn)

    from app.services.telegram import notify_credit
    await notify_credit(user=user, transaction=txn)

    return CreditApplyResult(
        credited=True,
        new_balance=Decimal(str(user.balance)),
        status="credited",
        transaction_id=txn.id,
    )


# ── Virtual-account (bank transfer) deposits ────────────────────────
#
# A user can fund their wallet by transferring money to their
# dedicated virtual account. Paystack reports that as a `charge.success`
# webhook, but unlike a Checkout top-up it carries no reference of
# ours — the gateway identifies the account by the *customer code*.
# Without this path the transfer was dropped as an orphan credit and
# the user's money silently disappeared.

def _dva_identifiers(data: dict) -> list[str]:
    """Pull the identifiers we can use to match a transfer to a
    virtual account out of the webhook payload.

    Paystack has used more than one shape for these events over time,
    so we collect every plausible identifier and let the database
    decide which one matches.
    """
    candidates: list[str] = []
    for key in ("customer", "customer_code", "dedicated_account_id", "account_id"):
        value = data.get(key)
        if value:
            candidates.append(str(value))
    dedicated = data.get("dedicated_account")
    if isinstance(dedicated, dict):
        for key in ("id", "customer", "customer_code", "account_number"):
            value = dedicated.get(key)
            if value:
                candidates.append(str(value))
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(candidates))


async def credit_virtual_account_deposit(
    session: Session,
    *,
    event: WebhookEvent,
) -> Optional[CreditApplyResult]:
    """Credit a wallet when money is transferred into its virtual account.

    Returns `None` when the event does not look like a virtual-account
    deposit, or when no virtual account matches — the caller then
    records the orphan-credit audit row.

    The credit is idempotent: the transaction is created with the
    gateway's reference, and a replayed webhook is rejected earlier by
    the `webhook_events` dedup.
    """
    data = (event.raw or {}).get("data") or {}
    if not isinstance(data, dict):
        return None

    identifiers = _dva_identifiers(data)
    if not identifiers:
        return None

    from app.models.virtual_account import VirtualAccount

    va = session.exec(
        select(VirtualAccount).where(
            (VirtualAccount.provider_customer_code.in_(identifiers))
            | (VirtualAccount.provider_account_reference.in_(identifiers))
        )
    ).first()
    if va is None:
        return None

    user = session.get(User, va.user_id)
    if user is None:
        logger.warning(
            "Virtual-account deposit %s: user %d not found",
            event.provider_reference, va.user_id,
        )
        return None

    amount = Decimal(str(event.amount_kobo or 0)) / Decimal(100)
    reference = event.provider_reference or f"dva_{va.id}_{event.event_id}"

    # A transfer can only be credited once. If we already have a
    # transaction for this reference, leave the balance alone.
    existing = session.exec(
        select(Transaction).where(Transaction.provider_reference == reference)
    ).first()
    if existing is not None:
        logger.info(
            "Virtual-account deposit %s already recorded; not crediting twice",
            reference,
        )
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="already_credited",
            transaction_id=existing.id,
        )

    txn = Transaction(
        user_id=user.id,
        type=TransactionType.CREDIT.value,
        amount=amount,
        fee=Decimal("0.00"),
        currency=va.currency or "NGN",
        status=TransactionStatus.SUCCESS.value,
        provider="paystack",
        provider_reference=reference,
        narration="Deposit to virtual account",
    )
    session.add(txn)

    user.balance = Decimal(str(user.balance)) + amount
    session.add(user)
    session.flush()

    audit_wallet_credit(
        session,
        user_id=user.id,
        amount=float(amount),
        provider_reference=reference,
        new_balance=float(user.balance),
    )
    write_audit(
        session,
        actor=AuditActor.WEBHOOK,
        event_type=AuditEventType.WALLET_CREDITED,
        user_id=user.id,
        entity_type=AuditEntityType.VIRTUAL_ACCOUNT,
        entity_id=va.id,
        metadata={
            "source": "virtual_account_deposit",
            "reference": reference,
            "amount_kobo": event.amount_kobo,
            "account_number": va.account_number,
        },
    )
    record_topup_credited(source="dva")
    session.commit()
    session.refresh(user)
    session.refresh(txn)

    from app.services.telegram import notify_credit

    await notify_credit(user=user, transaction=txn)

    return CreditApplyResult(
        credited=True,
        new_balance=Decimal(str(user.balance)),
        status="credited",
        transaction_id=txn.id,
    )


# ── Top-up via Paystack Checkout (Paystack-only counterpart of the
#    Nomba-only `start_topup` that lives in the sibling project) ──


async def start_topup(
    session: Session,
    *,
    user: User,
    amount: Decimal,
    provider: PaymentProvider,
    callback_url: str | None = None,
) -> TopupStartResult:
    """Mint a unique reference, persist a pending `Transaction` row,
    call `provider.initialize_topup(...)`, write the audit row, bump
    the metrics counter. Returns the URL the user opens to pay.

    Raises:
        TopupValidationError: amount is below `MIN_TOPUP_NGN` or above
            `MAX_TOPUP_NGN`.
        PaymentError: the provider refused to start the top-up (bad
            key, network error, etc.). The pending `Transaction` row
            is rolled back so the user can retry.
    """
    amount = Decimal(str(amount))
    if amount < MIN_TOPUP_NGN:
        raise TopupValidationError(
            f"Minimum top-up is {MIN_TOPUP_NGN} NGN."
        )
    if amount > MAX_TOPUP_NGN:
        raise TopupValidationError(
            f"Maximum top-up is {MAX_TOPUP_NGN} NGN. "
            "Contact support for larger amounts."
        )

    reference = f"topup_{user.id}_{secrets.token_hex(8)}"
    amount_kobo = int((amount * Decimal(100)).quantize(Decimal("1")))

    txn = Transaction(
        user_id=user.id,
        type=TransactionType.CREDIT.value,
        amount=amount,
        fee=Decimal("0.00"),
        currency="NGN",
        status=TransactionStatus.PENDING.value,
        provider=provider.name,
        provider_reference=reference,
        narration=f"Top-up via {provider.name} Checkout",
    )
    session.add(txn)
    session.flush()

    try:
        init: TopupInit = await provider.initialize_topup(
            amount_kobo=amount_kobo,
            email=user.email,
            reference=reference,
            callback_url=callback_url,
        )
    except PaymentError as exc:
        session.delete(txn)
        session.commit()
        logger.warning("Topup init failed for user %d: %s", user.id, exc)
        raise

    # Paystack echoes our reference back, so this is the same as
    # `txn.provider_reference` — we still write it to keep the
    # column populated for queries/analytics and for forward-
    # compatibility with a future provider that uses its own ID.
    if init.reference:
        txn.provider_order_reference = init.reference
        session.add(txn)
        session.commit()
        session.refresh(txn)

    write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.WALLET_CREDITED,
        user_id=user.id,
        entity_type=AuditEntityType.TRANSACTION,
        entity_id=txn.id,
        metadata={
            "trigger": "topup_init",
            "reference": reference,
            "provider_order_reference": txn.provider_order_reference,
            "amount": float(amount),
            "status": "pending",
        },
    )
    session.commit()

    record_topup_initiated()

    return TopupStartResult(
        authorization_url=init.authorization_url,
        reference=reference,
        transaction_id=txn.id or 0,
        amount=amount,
        currency="NGN",
        provider=provider.name,
    )


async def verify_pending_topup(
    session: Session,
    *,
    user: User,
    reference: str,
    provider: PaymentProvider,
) -> CreditApplyResult:
    """Manually verify a pending top-up by calling the provider's
    transaction-status endpoint and applying the credit if settled.

    Used by `POST /wallet/topup/verify` (the user-facing "I've paid"
    button). Returns a `CreditApplyResult` so the caller can render
    a useful message.

    The `reference` argument is matched against EITHER our internal
    `provider_reference` or the provider's `provider_order_reference`
    so the user can use whichever they remember.
    """
    from sqlalchemy import or_

    txn = session.exec(
        select(Transaction).where(
            Transaction.user_id == user.id,
            or_(
                Transaction.provider_reference == reference,
                Transaction.provider_order_reference == reference,
            ),
        )
    ).first()
    if txn is None:
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="unknown_reference",
        )

    if txn.status == TransactionStatus.SUCCESS.value:
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="already_credited",
            transaction_id=txn.id,
        )

    if txn.status == TransactionStatus.FAILED.value:
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="already_failed",
            transaction_id=txn.id,
        )

    provider_ref = (
        txn.provider_order_reference
        or txn.provider_reference
        or reference
    )

    try:
        result: TransactionStatusResult = await provider.get_transaction(
            reference=provider_ref,
        )
    except PaymentError as exc:
        logger.warning(
            "get_transaction failed for user %d reference %s: %s",
            user.id, provider_ref, exc,
        )
        return CreditApplyResult(
            credited=False,
            new_balance=Decimal(str(user.balance)),
            status="unknown_reference",
            transaction_id=txn.id,
        )

    if result.status == "success":
        # Always credit `txn.amount` (what the user agreed to pay).
        # The provider's amount is used only as a divergence guard
        # so we never credit based on a different value.
        txn_amount_kobo = int(
            (Decimal(str(txn.amount)) * Decimal(100)).quantize(Decimal("1"))
        )
        if (
            result.amount_kobo is not None
            and result.amount_kobo != txn_amount_kobo
        ):
            logger.warning(
                "amount divergence for reference %s: provider=%d kobo, "
                "local=%d kobo (txn.amount=%s). Refusing to credit.",
                txn.provider_reference,
                result.amount_kobo,
                txn_amount_kobo,
                txn.amount,
            )
            txn.status = TransactionStatus.FAILED.value
            txn.failure_reason = "amount_mismatch"
            session.add(txn)
            write_audit(
                session,
                actor=AuditActor.USER,
                event_type=AuditEventType.WALLET_CREDITED,
                user_id=user.id,
                entity_type=AuditEntityType.TRANSACTION,
                entity_id=txn.id,
                metadata={
                    "reference": txn.provider_reference,
                    "provider_amount_kobo": result.amount_kobo,
                    "txn_amount_kobo": txn_amount_kobo,
                    "status": "amount_mismatch",
                },
            )
            session.commit()
            return CreditApplyResult(
                credited=False,
                new_balance=Decimal(str(user.balance)),
                status="amount_mismatch",
                transaction_id=txn.id,
            )

        event = WebhookEvent(
            event_type="charge.success",
            provider_reference=txn.provider_reference,
            event_id=f"verify:{txn.provider_reference}",
            amount_kobo=txn_amount_kobo,
            provider=provider.name,
        )
        return await apply_credit_from_provider_event(
            session, user=user, event=event,
        )

    return CreditApplyResult(
        credited=False,
        new_balance=Decimal(str(user.balance)),
        status=f"provider_{result.status}",
        transaction_id=txn.id,
    )
