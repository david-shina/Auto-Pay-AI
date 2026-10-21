"""Payment provider package.

`PaymentProvider` is the only interface business code should depend on.
The factory `get_payment_provider()` returns the configured Paystack
implementation. (The previous Nomba provider has been moved to the
standalone `nomba-gateway` sibling project.)
"""
from app.services.payments.base import (
    PaymentProvider,
    ResolvedAccount,
    TopupInit,
    TransactionStatusResult,
    TransferResult,
    VirtualAccountData,
    WebhookEvent,
)
from app.services.payments.exceptions import (
    AccountNameMismatchError,
    AuthenticationError,
    InsufficientFundsError,
    InvalidAccountError,
    KYCRequiredError,
    PaymentError,
    ProviderError,
    WebhookSignatureError,
)
from app.services.payments.paystack import PaystackProvider, get_payment_provider

__all__ = [
    "AccountNameMismatchError",
    "AuthenticationError",
    "InsufficientFundsError",
    "InvalidAccountError",
    "KYCRequiredError",
    "PaymentError",
    "PaymentProvider",
    "PaystackProvider",
    "ProviderError",
    "ResolvedAccount",
    "TopupInit",
    "TransactionStatusResult",
    "TransferResult",
    "VirtualAccountData",
    "WebhookEvent",
    "WebhookSignatureError",
    "get_payment_provider",
]
