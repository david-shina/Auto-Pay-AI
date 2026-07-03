"""Payment provider abstraction.

We never want to lock the business logic to a single gateway. The
`PaymentProvider` Protocol below is the only interface business code
should depend on. Concrete implementations live alongside this file
(`paystack.py`, etc.).

DTOs are kept as `dataclass(frozen=True)` (not Pydantic) because:
  * they are pure data crossing an internal boundary, no validation
    needed beyond typing;
  * they must be cheap to construct in tests;
  * they are returned by *the provider* and validated by the caller,
    so the provider never has to know our app's request validation
    rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable


# ── DTOs ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VirtualAccountData:
    """A dedicated virtual account (DVA) issued to a user by the provider.

    `provider_reference` is the gateway's ID for this DVA (e.g. Paystack
    `dedicated_account_id`). It is what we store on `virtual_accounts`
    as the FK to gateway reality.
    """

    account_number: str
    account_name: str
    bank_name: str
    bank_code: str
    provider_reference: str  # gateway-side ID
    provider: str  # "paystack", "flutterwave", ...


@dataclass(frozen=True)
class ResolvedAccount:
    """Result of "look up the name behind this account number"."""

    account_number: str
    account_name: str
    bank_code: str


@dataclass(frozen=True)
class TransferResult:
    """The provider's response when we initiated a transfer (payout)."""

    provider_reference: str  # our reference that the provider echoed back
    provider_transfer_id: str  # gateway's transfer ID
    status: str  # "pending" | "success" | "failed" | "reversed"
    raw_response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TopupInit:
    """Result of starting a top-up. The user is redirected to
    `authorization_url`; Paystack fires `charge.success` on completion
    and our existing webhook handler credits the wallet.
    """

    authorization_url: str  # Paystack-hosted checkout page
    reference: str  # our unique tx reference (used as provider_reference on the Transaction row)
    access_code: Optional[str] = None  # Paystack's own reference; not strictly needed


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook from the provider.

    `provider_reference` ties the event back to our own records
    (transaction.provider_reference, bill.provider_reference, etc.).
    `event_type` is normalized to a small closed set so business code
    can switch on it safely.
    `event_id` is a stable id from the provider used to dedup retries —
    either the provider's `event.id` or a SHA-256 of the raw body when
    the provider omits the field.
    """

    event_type: str  # "charge.success" | "transfer.success" | "transfer.failed" | "transfer.reversed" | "dedicatedaccount.assign.success"
    provider_reference: str
    event_id: str  # provider's event.id, or a body hash for dedup
    amount_kobo: Optional[int] = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TopupInit:
    """A provider-hosted top-up flow that the user is about to enter.

    `authorization_url` is where the user is redirected (Paystack's
    hosted page, Stripe Checkout, etc.). `reference` is the provider's
    own reference for the transaction; the same value is echoed in the
    `charge.success` webhook and is the lookup key our handler uses
    to credit the matching pending `Transaction` row in our DB.
    `access_code` is provider-specific (Paystack returns it; Stripe
    etc. may not) and is useful for inline / popup integrations.
    """

    authorization_url: str
    reference: str
    provider: str  # "paystack" | future
    access_code: Optional[str] = None


# ── Protocol ────────────────────────────────────────────────────────

@runtime_checkable
class PaymentProvider(Protocol):
    """The contract every payment-gateway implementation must satisfy."""

    name: str  # "paystack" | "flutterwave" | ...

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: Optional[str] = None,
    ) -> str:
        """Create a customer at the provider; return provider's customer_id/code."""
        ...

    async def create_virtual_account(
        self,
        *,
        customer_code: str,
        preferred_bank: Optional[str] = None,
    ) -> VirtualAccountData:
        """Issue a dedicated virtual account for `customer_code`."""
        ...

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        """Look up the name on `account_number` at `bank_code`."""
        ...

    async def create_transfer_recipient(
        self,
        *,
        account_number: str,
        bank_code: str,
        account_name: str,
    ) -> str:
        """Create a transfer recipient; return provider's recipient_code."""
        ...

    async def initiate_transfer(
        self,
        *,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str,
    ) -> TransferResult:
        """Move `amount_kobo` (1 NGN = 100 kobo) from our balance to recipient."""
        ...

    async def initialize_topup(
        self,
        *,
        amount_kobo: int,
        email: str,
        reference: str,
        callback_url: Optional[str] = None,
    ) -> TopupInit:
        """Start a hosted top-up flow. The user is redirected to the
        returned `authorization_url`, pays via card / bank / USSD / QR,
        and Paystack fires `charge.success` on completion. Our webhook
        handler (`_handle_charge_success`) credits the wallet using
        `reference` as the lookup key.
        """
        ...

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> bool:
        """Return True iff `signature_header` is a valid HMAC of `raw_body`."""
        ...

    async def parse_webhook(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> WebhookEvent:
        """Verify signature, then parse into a `WebhookEvent`. Raises on bad sig."""
        ...
