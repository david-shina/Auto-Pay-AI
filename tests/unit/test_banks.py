"""Unit tests for bank-name normalisation.

Pure helpers only - the DB-backed resolution is exercised in
`tests/integration/test_banks_directory.py`, matching the project's
split between unit and integration tests.
"""
from __future__ import annotations

import pytest

from app.services.banks import _is_bank_code, _normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GTBank", "gtbank"),
        ("Guaranty Trust Bank", "guaranty trust"),
        ("Guaranty Trust Bank Plc", "guaranty trust"),
        ("Access Bank (Diamond)", "access diamond"),
        ("UBA", "uba"),
        ("First Bank of Nigeria", "first"),
        ("  United   Bank   For Africa  ", "united for africa"),
        ("Ecobank Nigeria", "ecobank"),
        ("Nova Merchant Bank", "nova merchant"),
        ("Stanbic IBTC", "stanbic ibtc"),
    ],
)
def test_normalize_strips_noise_and_punctuation(raw: str, expected: str) -> None:
    assert _normalize(raw) == expected


def test_normalize_keeps_raw_tokens_when_everything_is_noise() -> None:
    # "Bank" is entirely noise words; falling back to the raw tokens
    # keeps the value matchable instead of collapsing to "".
    assert _normalize("Bank") == "bank"


def test_normalize_handles_accents() -> None:
    assert _normalize("Café Bank") == "cafe"


@pytest.mark.parametrize(
    "value",
    ["058", "044", "50211", "C08", "035A", "MFB50094", "00zap"],
)
def test_is_bank_code_accepts_real_paystack_codes(value: str) -> None:
    # Paystack issues alphanumeric codes, not just digits.
    assert _is_bank_code(value) is True


@pytest.mark.parametrize("value", ["", "12", "MFB51116M2", "058 044", "GT-Bank!"])
def test_is_bank_code_rejects_wrong_shapes(value: str) -> None:
    assert _is_bank_code(value) is False
