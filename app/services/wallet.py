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
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from decimal import Decimal

from sqlmodel import Session

from app.core.metrics import record_topup_initiated
from app.models.enums import (
    AuditActor,
    AuditEntityType,
    AuditEventType,
    TransactionStatus,
    TransactionType,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.services.audit import write_audit
from app.services.payments import (
    PaymentError,
    PaymentProvider,
    TopupInit,
)

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
            "amount": float(amount),
            "status": "pending",
        },
    )
    session.commit()

    record_topup_initiated()

    return TopupStartResult(
        authorization_url=init.authorization_url,
        reference=init.reference,
        transaction_id=txn.id or 0,
        amount=amount,
        currency="NGN",
        provider=provider.name,
    )


__all__ = [
    "MIN_TOPUP_NGN",
    "MAX_TOPUP_NGN",
    "TopupValidationError",
    "TopupStartResult",
    "start_topup",
]
