"""Paystack implementation of the PaymentProvider protocol.

Reference: https://paystack.com/docs/api/
All amounts are in KOBO (1 NGN = 100 kobo). We pass amounts as `int`
to the API and let Paystack format them. Returning them as `int` to
callers keeps the rest of the codebase in integer-kobo until the
moment we format for the user.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import get_settings
from app.services.payments.base import (
    BankInfo,
    PaymentProvider,
    ResolvedAccount,
    TopupInit,
    TransferResult,
    VirtualAccountData,
    WebhookEvent,
)
from app.services.payments.exceptions import (
    AccountNameMismatchError,
    AuthenticationError,
    InvalidAccountError,
    KYCRequiredError,
    PaymentError,
    ProviderError,
    WebhookSignatureError,
)

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from app.services.payments.base import TransactionStatusResult

logger = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"

# Paystack transaction statuses, normalised to ours. `abandoned` means the
# user never completed payment (no retry from us), so it maps to `failed`.
_PAYSTACK_STATUS_MAP = {
    "success": "success",
    "pending": "pending",
    "failed": "failed",
    "abandoned": "failed",
    "reversed": "reversed",
}


# ── Paystack-specific error mapping ─────────────────────────────────

def _map_paystack_error(status_code: int, body: dict) -> PaymentError:
    """Translate a Paystack error JSON into the right typed exception.

    Paystack's `message` is human-readable and inconsistent; we lean on
    the HTTP status code plus a small set of well-known messages
    (`"Invalid key"`, `"Account name mismatch"`, etc.) to pick the
    exception class.
    """
    message = body.get("message") or "Unknown Paystack error"
    raw = body.get("data") or {}

    if status_code in (401, 403) or "Invalid key" in message:
        return AuthenticationError(message, provider="paystack", raw=raw)

    if "Account name" in message and "mismatch" in message.lower():
        return AccountNameMismatchError(message, provider="paystack", raw=raw)

    if "Insufficient" in message or "balance" in message.lower():
        # Paystack says "You have insufficient funds" when our merchant
        # balance is too low — rare in practice but possible.
        from app.services.payments.exceptions import InsufficientFundsError
        return InsufficientFundsError(message, provider="paystack", raw=raw)

    if "KYC" in message or "BVN" in message or "identity" in message.lower():
        return KYCRequiredError(message, provider="paystack", raw=raw)

    # Bank-code problems are the user's to fix (wrong or unknown bank),
    # so they must surface as a 422 rather than a retryable 502. Paystack
    # phrases these as "Unknown bank code", "Invalid bank code", or
    # "Could not find bank". Before this branch existed they fell through
    # to `ProviderError`, and the bot printed the raw text to the user -
    # which is where the "Unknown bank code" report came from.
    if "bank code" in message.lower() or "find bank" in message.lower():
        return InvalidAccountError(message, provider="paystack", raw=raw)

    if "resolve" in message.lower() or "invalid account" in message.lower():
        return InvalidAccountError(message, provider="paystack", raw=raw)

    return ProviderError(message, provider="paystack", raw=raw)


# ── Provider implementation ─────────────────────────────────────────

class PaystackProvider(PaymentProvider):
    name = "paystack"

    def __init__(
        self,
        *,
        secret_key: str,
        timeout: float = 30.0,
        base_url: str | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._timeout = timeout
        self._base_url = (base_url or PAYSTACK_BASE).rstrip("/")
        # We open the client lazily so tests that never call a method
        # don't pay the connection-pool cost.
        self._client: httpx.AsyncClient | None = None

    async def _client_lazy(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        return_full: bool = False,
    ) -> dict | list:
        """Make a Paystack call and return the `data` payload.

        Raises a typed `PaymentError` subclass on any non-2xx response.
        Raises `ProviderError` on transport failures.

        `return_full=True` returns the whole envelope instead of just
        `data`, which the cursor-paginated list endpoints need so the
        caller can read `meta.next`.
        """
        if not self._secret_key:
            raise AuthenticationError(
                "PAYSTACK_SECRET_KEY is not configured; cannot call Paystack.",
                provider="paystack",
            )
        client = await self._client_lazy()
        try:
            resp = await client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Paystack transport error: {exc}", provider="paystack"
            ) from exc

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Paystack returned non-JSON ({resp.status_code})",
                provider="paystack",
            ) from exc

        if not body.get("status", False):
            raise _map_paystack_error(resp.status_code, body)

        if return_full:
            return body
        return body.get("data") or {}

    # ── Customer + DVA ──────────────────────────────────────────────

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: str | None = None,
    ) -> str:
        data = await self._request(
            "POST",
            "/customer",
            json_body={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                **({"phone": phone} if phone else {}),
            },
        )
        return str(data["customer_code"])

    async def create_virtual_account(
        self,
        *,
        customer_code: str,
        preferred_bank: str | None = None,
    ) -> VirtualAccountData:
        payload: dict[str, Any] = {"customer": customer_code}
        if preferred_bank:
            payload["preferred_bank"] = preferred_bank

        data = await self._request("POST", "/dedicated_account", json_body=payload)

        return VirtualAccountData(
            account_number=str(data["account_number"]),
            account_name=str(data["account_name"]),
            bank_name=str(data["bank"]["name"]),
            bank_code=str(data["bank"]["id"]),
            provider_reference=str(data["id"]),  # dedicated_account_id
            provider=self.name,
        )

    # ── Bank directory ──────────────────────────────────────────────

    async def list_banks(self, *, country: str = "nigeria") -> list[BankInfo]:
        """Fetch every supported bank via `GET /bank`.

        Paystack's bank endpoint has no name-search parameter, so we
        pull the whole list (cursor-paginated, 100 per page) and match
        locally. The list is ~500 rows and changes rarely.
        """
        banks: list[BankInfo] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "country": country,
                "perPage": 100,
                "use_cursor": "true",
            }
            if cursor:
                params["next"] = cursor

            body = await self._request(
                "GET", "/bank", params=params, return_full=True
            )
            if not isinstance(body, dict):
                break

            for row in body.get("data") or []:
                code = str(row.get("code") or "").strip()
                name = str(row.get("name") or "").strip()
                if not code or not name:
                    continue
                slug = row.get("slug")
                bank_type = row.get("type")
                banks.append(
                    BankInfo(
                        code=code,
                        name=name,
                        slug=str(slug).strip() or None if slug else None,
                        type=str(bank_type).strip() or None if bank_type else None,
                        country=str(row.get("country") or country).lower(),
                        active=bool(row.get("active", True))
                        and not bool(row.get("is_deleted", False)),
                    )
                )

            cursor = (body.get("meta") or {}).get("next")
            if not cursor:
                break

        return banks

    # ── Account resolution + transfers ──────────────────────────────

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        data = await self._request(
            "GET",
            "/bank/resolve",
            params={"account_number": account_number, "bank_code": bank_code},
        )
        # Paystack's `/bank/resolve` returns ONLY `account_number` and
        # `account_name` - it does not echo the bank code back. Reading
        # `data["bank_code"]` raised KeyError on every real call; the
        # test fixtures fabricated the field, which is why it survived.
        account_name = str(data.get("account_name") or "").strip()
        if not account_name:
            raise InvalidAccountError(
                f"Paystack returned no account name for {account_number} "
                f"at bank {bank_code}",
                provider="paystack",
                raw=data,
            )
        return ResolvedAccount(
            account_number=str(data.get("account_number") or account_number),
            account_name=account_name,
            bank_code=str(data.get("bank_code") or bank_code),
        )

    async def create_transfer_recipient(
        self,
        *,
        account_number: str,
        bank_code: str,
        account_name: str,
    ) -> str:
        data = await self._request(
            "POST",
            "/transferrecipient",
            json_body={
                "type": "nuban",
                "name": account_name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
            },
        )
        return str(data["recipient_code"])

    async def initiate_transfer(
        self,
        *,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str,
    ) -> TransferResult:
        data = await self._request(
            "POST",
            "/transfer",
            json_body={
                "source": "balance",
                "amount": amount_kobo,
                "recipient": recipient_code,
                "reference": reference,
                "reason": reason,
                "currency": "NGN",
            },
        )
        return TransferResult(
            provider_reference=str(data.get("reference") or reference),
            provider_transfer_id=str(data.get("id") or ""),
            status=str(data.get("status") or "pending").lower(),
            raw_response=data,
            # Paystack returns `transfer_code` ("TRF_...") when the
            # transfer is parked awaiting OTP. `finalize_transfer`
            # needs that, not the numeric `id`.
            transfer_code=(
                str(data["transfer_code"]) if data.get("transfer_code") else None
            ),
        )

    async def finalize_transfer(
        self,
        *,
        transfer_code: str,
        otp: str,
    ) -> TransferResult:
        """Approve an `otp`-parked transfer with the OTP the merchant received."""
        data = await self._request(
            "POST",
            "/transfer/finalize_transfer",
            json_body={"transfer_code": transfer_code, "otp": otp},
        )
        return TransferResult(
            provider_reference=str(data.get("reference") or ""),
            provider_transfer_id=str(data.get("id") or ""),
            status=str(data.get("status") or "pending").lower(),
            raw_response=data,
            transfer_code=str(data.get("transfer_code") or transfer_code),
        )

    async def get_transfer(
        self,
        *,
        reference: str,
    ) -> TransactionStatusResult:
        """Look up an outgoing transfer.

        Transfers have their own verify endpoint; `/transaction/verify`
        only covers charges and would report every transfer as unknown.
        """
        from app.services.payments.base import TransactionStatusResult

        data = await self._request("GET", f"/transfer/verify/{reference}")

        # Transfer-specific statuses. `otp` and `queued` mean the money
        # has not moved yet, so they are "pending", not terminal.
        mapping = {
            "success": "success",
            "failed": "failed",
            "reversed": "reversed",
            "abandoned": "failed",
            "pending": "pending",
            "otp": "pending",
            "queued": "pending",
            "processing": "pending",
        }
        raw_status = str(data.get("status") or "").lower()
        normalized = mapping.get(raw_status, "unknown")

        amount_kobo: int | None = None
        raw_amount = data.get("amount")
        if raw_amount is not None:
            try:
                amount_kobo = int(raw_amount)
            except (TypeError, ValueError):
                amount_kobo = None

        return TransactionStatusResult(
            provider_reference=str(data.get("reference") or reference),
            status=normalized,
            amount_kobo=amount_kobo,
            raw=data,
        )

    # ── Top-up via Checkout (no DVA required) ─────────────────────

    async def initialize_topup(
        self,
        *,
        amount_kobo: int,
        email: str,
        reference: str,
        callback_url: str | None = None,
    ) -> TopupInit:
        """Start a hosted Checkout session. The user is redirected to
        `authorization_url`, pays via card / bank / USSD / QR, and on
        completion Paystack fires `charge.success` with `reference` as
        `data.reference` — our existing `_handle_charge_success` looks
        up a Transaction row by that reference and credits the wallet.
        """
        payload: dict[str, Any] = {
            "amount": amount_kobo,
            "email": email,
            "reference": reference,
            "currency": "NGN",
        }
        if callback_url:
            payload["callback_url"] = callback_url

        data = await self._request(
            "POST",
            "/transaction/initialize",
            json_body=payload,
        )
        authorization_url = str(data.get("authorization_url") or "")
        access_code = data.get("access_code")
        if not authorization_url:
            raise ProviderError(
                "Paystack did not return an authorization_url",
                provider="paystack",
            )
        return TopupInit(
            authorization_url=authorization_url,
            reference=reference,
            provider=self.name,
            access_code=str(access_code) if access_code else None,
        )

    # ── Transaction lookup (polling fallback) ──────────────────────

    async def get_transaction(
        self,
        *,
        reference: str,
    ) -> TransactionStatusResult:
        """Look up a single transaction by our reference.

        Paystack exposes `GET /transaction/verify/{reference}` which
        returns `{status, reference, amount, ...}`. We normalize
        `status` to the closed set
        `{"success", "pending", "failed", "reversed", "unknown"}`.

        Paystack's status values include `success`, `abandoned`,
        `failed`, `reversed`. We map `abandoned` → `failed` (the
        user never completed payment, no retry from us).
        """
        from app.services.payments.base import TransactionStatusResult

        data = await self._request(
            "GET",
            f"/transaction/verify/{reference}",
        )

        raw_status = str(data.get("status") or "").lower()
        normalized = _PAYSTACK_STATUS_MAP.get(raw_status, "unknown")

        amount_kobo: int | None = None
        raw_amount = data.get("amount")
        if raw_amount is not None:
            try:
                amount_kobo = int(raw_amount)
            except (TypeError, ValueError):
                amount_kobo = None

        provider_ref = str(data.get("reference") or reference)

        return TransactionStatusResult(
            provider_reference=provider_ref,
            status=normalized,
            amount_kobo=amount_kobo,
            raw=data,
        )

    # ── Webhook signature + parsing ─────────────────────────────────

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> bool:
        if not signature_header:
            return False
        digest = hmac.new(
            self._secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(digest, signature_header)

    async def parse_webhook(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> WebhookEvent:
        if not self.verify_webhook_signature(
            raw_body=raw_body, signature_header=signature_header
        ):
            raise WebhookSignatureError(
                "Invalid Paystack webhook signature", provider="paystack"
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookSignatureError(
                f"Webhook body is not valid JSON: {exc}", provider="paystack"
            ) from exc

        event_name = str(payload.get("event") or "")
        data = payload.get("data") or {}

        # Normalize event names. The Provider's webhook event string
        # is what business code switches on.
        provider_ref = ""
        amount_kobo: int | None = None

        if event_name == "charge.success" or event_name in ("transfer.success", "transfer.failed", "transfer.reversed"):
            provider_ref = str(data.get("reference") or "")
            amount_kobo = int(data.get("amount") or 0)
        elif event_name == "dedicatedaccount.assign.success":
            # Has no useful `reference`; the DVA's `id` is the lookup key.
            provider_ref = str(data.get("id") or "")
        else:
            # Unknown event — pass through with whatever reference we
            # can find, but log loudly so the team can decide.
            logger.warning("Unknown Paystack webhook event: %s", event_name)
            provider_ref = str(data.get("reference") or data.get("id") or "")

        # Stable id for dedup. Paystack's modern payloads have `id` at
        # the top level; older ones don't, so fall back to a SHA-256 of
        # the raw body. The first delivery of any event will compute
        # the same value; subsequent retries will collide on the
        # UNIQUE(provider, event_id) constraint in webhook_events.
        event_id = str(
            payload.get("id")
            or payload.get("event_id")
            or hashlib.sha256(raw_body).hexdigest()
        )

        return WebhookEvent(
            event_type=event_name,
            provider_reference=provider_ref,
            event_id=event_id,
            amount_kobo=amount_kobo,
            raw=payload,
        )


# ── Factory dependency for FastAPI ──────────────────────────────────

def get_payment_provider() -> PaymentProvider:
    """Build the configured Paystack provider.

    Swapped in tests via `app.dependency_overrides`. Paystack is now
    the only supported payment gateway in this project; the previous
    Nomba branch has moved to the standalone `nomba-gateway` project.
    """
    settings = get_settings()
    return PaystackProvider(
        secret_key=settings.paystack_secret_key,
        base_url=settings.paystack_base_url,
    )
