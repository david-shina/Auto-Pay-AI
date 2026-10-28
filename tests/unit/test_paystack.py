"""Tests for the Paystack provider.

We use `respx` to mock httpx calls so no real network is made.
The `verify_webhook_signature` test exercises real HMAC math (no mock).
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.services.payments import (
    AuthenticationError,
    InvalidAccountError,
    KYCRequiredError,
    PaystackProvider,
    ProviderError,
    WebhookSignatureError,
)

PAYSTACK = "https://api.paystack.co"
SECRET = "sk_test_d4e5f6a1b2c3d4e5f6a7b8c9d0e1f2a3"


def _client() -> PaystackProvider:
    p = PaystackProvider(secret_key=SECRET)
    return p


# ── create_customer ────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_create_customer_happy_path() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(
        return_value=httpx.Response(
            200, json={"status": True, "message": "Customer created", "data": {"customer_code": "CUS_x"}}
        )
    )
    p = _client()
    code = await p.create_customer(
        email="ada@example.com", first_name="Ada", last_name="Lovelace"
    )
    assert code == "CUS_x"


@pytest.mark.asyncio
@respx.mock
async def test_create_customer_auth_error() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(
        return_value=httpx.Response(401, json={"status": False, "message": "Invalid key", "data": None})
    )
    p = _client()
    with pytest.raises(AuthenticationError):
        await p.create_customer(
            email="ada@example.com", first_name="Ada", last_name="Lovelace"
        )


# ── create_virtual_account ─────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_create_virtual_account_parses_response() -> None:
    respx.post(f"{PAYSTACK}/dedicated_account").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "message": "Dedicated account created",
                "data": {
                    "id": 42,
                    "account_number": "0123456789",
                    "account_name": "Ada Lovelace",
                    "bank": {"id": "058", "name": "GTBank"},
                },
            },
        )
    )
    p = _client()
    va = await p.create_virtual_account(customer_code="CUS_x")
    assert va.account_number == "0123456789"
    assert va.bank_name == "GTBank"
    assert va.bank_code == "058"
    assert va.provider_reference == "42"
    assert va.provider == "paystack"


# ── resolve_account ────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_resolve_account() -> None:
    # Paystack's real response shape: no `bank_code` field. The old
    # fixture included one, which hid a KeyError on every real call.
    respx.get(f"{PAYSTACK}/bank/resolve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "message": "Account number resolved",
                "data": {
                    "account_number": "0123456789",
                    "account_name": "DSTV NG LTD",
                },
            },
        )
    )
    p = _client()
    out = await p.resolve_account(account_number="0123456789", bank_code="058")
    assert out.account_name == "DSTV NG LTD"
    assert out.account_number == "0123456789"
    # The requested code is echoed back, since Paystack omits it.
    assert out.bank_code == "058"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_account_requires_an_account_name() -> None:
    respx.get(f"{PAYSTACK}/bank/resolve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "message": "Account number resolved",
                "data": {"account_number": "0123456789"},
            },
        )
    )
    p = _client()
    with pytest.raises(InvalidAccountError):
        await p.resolve_account(account_number="0123456789", bank_code="058")


@pytest.mark.asyncio
@respx.mock
async def test_resolve_account_invalid() -> None:
    respx.get(f"{PAYSTACK}/bank/resolve").mock(
        return_value=httpx.Response(
            400, json={"status": False, "message": "Invalid account number", "data": None}
        )
    )
    p = _client()
    with pytest.raises(InvalidAccountError):
        await p.resolve_account(account_number="0000000000", bank_code="058")


# ── initiate_transfer ──────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_initiate_transfer() -> None:
    respx.post(f"{PAYSTACK}/transfer").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "id": 99,
                    "reference": "autopay_1_abc",
                    "status": "pending",
                    "amount": 500000,
                },
            },
        )
    )
    p = _client()
    out = await p.initiate_transfer(
        amount_kobo=500000,
        recipient_code="RCP_x",
        reference="autopay_1_abc",
        reason="AutoPay: DSTV",
    )
    assert out.provider_reference == "autopay_1_abc"
    assert out.provider_transfer_id == "99"
    assert out.status == "pending"


# ── Webhook signature: real HMAC math ──────────────────────────────

def test_verify_webhook_signature_valid() -> None:
    p = _client()
    body = b'{"event":"charge.success","data":{"reference":"r1"}}'
    expected = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert p.verify_webhook_signature(raw_body=body, signature_header=expected) is True


def test_verify_webhook_signature_invalid() -> None:
    p = _client()
    body = b'{"event":"charge.success"}'
    assert p.verify_webhook_signature(raw_body=body, signature_header="deadbeef") is False


def test_verify_webhook_signature_missing_header() -> None:
    p = _client()
    assert p.verify_webhook_signature(raw_body=b"x", signature_header="") is False


def test_verify_webhook_signature_tampered_body() -> None:
    p = _client()
    body = b'{"event":"charge.success","data":{"amount":1000}}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    tampered = body.replace(b"1000", b"99999")
    assert p.verify_webhook_signature(raw_body=tampered, signature_header=sig) is False


# ── parse_webhook ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_webhook_charge_success() -> None:
    p = _client()
    payload = {
        "event": "charge.success",
        "data": {"reference": "r1", "amount": 15000},
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "charge.success"
    assert event.provider_reference == "r1"
    assert event.amount_kobo == 15000


@pytest.mark.asyncio
async def test_parse_webhook_transfer_success() -> None:
    p = _client()
    payload = {
        "event": "transfer.success",
        "data": {"id": 5, "reference": "autopay_1_x", "amount": 100000},
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "transfer.success"
    assert event.provider_reference == "autopay_1_x"
    assert event.amount_kobo == 100000


@pytest.mark.asyncio
async def test_parse_webhook_rejects_bad_signature() -> None:
    p = _client()
    raw = b'{"event":"charge.success","data":{"reference":"r1"}}'
    with pytest.raises(WebhookSignatureError):
        await p.parse_webhook(raw_body=raw, signature_header="bad-sig")


@pytest.mark.asyncio
async def test_parse_webhook_rejects_non_json() -> None:
    p = _client()
    raw = b"not json at all"
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    with pytest.raises(WebhookSignatureError):
        await p.parse_webhook(raw_body=raw, signature_header=sig)


@pytest.mark.asyncio
async def test_parse_webhook_unknown_event_passes_through() -> None:
    p = _client()
    payload = {"event": "satellite.launched", "data": {"id": 1}}
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "satellite.launched"
    assert event.provider_reference == "1"


# ── transport error path ────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_network_error_wrapped_as_provider_error() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(side_effect=httpx.ConnectError("nope"))
    p = _client()
    with pytest.raises(ProviderError):
        await p.create_customer(
            email="x@x.com", first_name="x", last_name="y"
        )


# ── error mapping extras ───────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_kyc_required_error_mapping() -> None:
    respx.post(f"{PAYSTACK}/transfer").mock(
        return_value=httpx.Response(
            400, json={"status": False, "message": "BVN is required for this transfer", "data": None}
        )
    )
    p = _client()
    with pytest.raises(KYCRequiredError):
        await p.initiate_transfer(
            amount_kobo=100, recipient_code="R", reference="r", reason="x"
        )


# ── list_banks ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_banks_follows_cursor_pagination() -> None:
    route = respx.get(f"{PAYSTACK}/bank").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "status": True,
                    "data": [
                        {
                            "code": "058",
                            "name": "Guaranty Trust Bank",
                            "slug": "guaranty-trust-bank",
                            "type": "nuban",
                            "country": "Nigeria",
                            "active": True,
                        },
                        {
                            "code": "C08",
                            "name": "VTNetworks",
                            "slug": "vtnetworks-ng",
                            "type": "nuban",
                            "country": "Nigeria",
                            "active": True,
                        },
                    ],
                    "meta": {"next": "cursor-2"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "status": True,
                    "data": [
                        {
                            "code": "044",
                            "name": "Access Bank",
                            "slug": "access-bank",
                            "type": "nuban",
                            "country": "Nigeria",
                            "active": True,
                        },
                        {
                            "code": "999",
                            "name": "Delisted Bank",
                            "slug": "delisted",
                            "type": "nuban",
                            "country": "Nigeria",
                            "active": False,
                            "is_deleted": True,
                        },
                    ],
                    "meta": {"next": None},
                },
            ),
        ]
    )

    p = _client()
    banks = await p.list_banks()

    assert [b.code for b in banks] == ["058", "C08", "044", "999"]
    # Alphanumeric codes must survive untouched.
    assert banks[1].code == "C08"
    # `is_deleted` marks the row inactive even when `active` is absent.
    assert banks[3].active is False

    assert route.call_count == 2
    assert route.calls[0].request.url.params["use_cursor"] == "true"
    assert route.calls[0].request.url.params["perPage"] == "100"
    assert route.calls[1].request.url.params["next"] == "cursor-2"


# ── bank-code error mapping ────────────────────────────────────────

@pytest.mark.parametrize(
    "message",
    ["Unknown bank code", "Invalid bank code", "Could not find bank"],
)
def test_bank_code_errors_are_user_fixable(message: str) -> None:
    """A bad bank code must be a 422, not a retryable 502.

    Regression: these fell through to `ProviderError`, so the bot
    printed the raw provider text ("Unknown bank code") to the user and
    the API told them to retry - which could never help.
    """
    from app.services.payments.paystack import _map_paystack_error

    exc = _map_paystack_error(400, {"message": message, "data": None})
    assert isinstance(exc, InvalidAccountError)


def test_generic_provider_error_stays_retryable() -> None:
    from app.services.payments.paystack import _map_paystack_error

    exc = _map_paystack_error(500, {"message": "Internal server error", "data": None})
    assert isinstance(exc, ProviderError)
    assert not isinstance(exc, InvalidAccountError)


# ── finalize / verify transfers ────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_finalize_transfer_success() -> None:
    route = respx.post(f"{PAYSTACK}/transfer/finalize_transfer").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "id": 1,
                    "reference": "autopay_1_abc",
                    "transfer_code": "TRF_abc",
                    "status": "success",
                },
            },
        )
    )
    p = _client()
    out = await p.finalize_transfer(transfer_code="TRF_abc", otp="123456")

    assert out.status == "success"
    assert out.provider_reference == "autopay_1_abc"
    assert out.transfer_code == "TRF_abc"
    body = json.loads(route.calls[0].request.content)
    assert body == {"transfer_code": "TRF_abc", "otp": "123456"}


@pytest.mark.asyncio
@respx.mock
async def test_get_transfer_maps_otp_to_pending() -> None:
    respx.get(f"{PAYSTACK}/transfer/verify/autopay_1_abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "reference": "autopay_1_abc",
                    "status": "otp",
                    "amount": 500000,
                },
            },
        )
    )
    p = _client()
    out = await p.get_transfer(reference="autopay_1_abc")

    # "otp" means the money has not moved yet.
    assert out.status == "pending"
    assert out.amount_kobo == 500000


@pytest.mark.asyncio
@respx.mock
async def test_get_transfer_success_and_failure() -> None:
    route = respx.get(f"{PAYSTACK}/transfer/verify/ref-1")
    p = _client()

    route.mock(return_value=httpx.Response(200, json={"status": True, "data": {"status": "success"}}))
    assert (await p.get_transfer(reference="ref-1")).status == "success"

    route.mock(return_value=httpx.Response(200, json={"status": True, "data": {"status": "failed"}}))
    assert (await p.get_transfer(reference="ref-1")).status == "failed"


@pytest.mark.asyncio
@respx.mock
async def test_get_transfer_unknown_status() -> None:
    respx.get(f"{PAYSTACK}/transfer/verify/ref-2").mock(
        return_value=httpx.Response(200, json={"status": True, "data": {"status": "weird"}})
    )
    p = _client()
    assert (await p.get_transfer(reference="ref-2")).status == "unknown"
