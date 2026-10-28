"""Integration tests: bank names supplied at bill-creation time.

Regression cover for the "Unknown bank code" bug: a bill whose bank was
supplied as a *name* used to store the name in `bank_code` and fail
later at payout. The API now resolves it up front, or rejects it with a
message naming the candidates.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _signup(c: TestClient) -> str:
    stamp = int(time.time())
    r = c.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Bill",
            "last_name": "Bank",
            "email": f"billbank_{stamp}_{id(c)}@x.com",
            "phone_number": f"+1{str(stamp + 7)[-10:]}",
            "password": "Secret123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _payload(**overrides) -> dict:
    payload = {
        "vendor_name": "DSTV",
        "amount": 5000,
        "due_date": (datetime.now() + timedelta(days=3)).isoformat(),
        "account_number": "0123456789",
        "bank_code": "058",
    }
    payload.update(overrides)
    return payload


def test_bill_created_with_a_bank_name_stores_the_code(client: TestClient) -> None:
    token = _signup(client)
    r = client.post(
        "/api/v1/bills",
        json=_payload(bank_code="GTBank"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    bill = r.json()["bill"]
    assert bill["bank_code"] == "058"
    assert bill["bank_name"] == "Guaranty Trust Bank"


def test_bill_created_with_an_unknown_bank_is_rejected(client: TestClient) -> None:
    token = _signup(client)
    r = client.post(
        "/api/v1/bills",
        json=_payload(bank_code="ZZZQQQ Not A Bank"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
    assert "not a recognised bank" in r.json()["detail"]


def test_bill_created_with_a_valid_code_is_unchanged(client: TestClient) -> None:
    token = _signup(client)
    r = client.post(
        "/api/v1/bills",
        json=_payload(bank_code="058"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    bill = r.json()["bill"]
    assert bill["bank_code"] == "058"
    assert bill["bank_name"] == "Guaranty Trust Bank"
