"""Integration tests for the bank directory (DB-backed resolution).

The `session` fixture is bound to the real test database. `banks` is
reference data and is deliberately NOT in the truncate list, so the
snapshot seed survives between tests.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session

from app.services.banks import get_bank_directory


@pytest.fixture
def directory(session: Session):
    d = get_bank_directory()
    d.ensure_loaded(session)
    return d


def test_snapshot_seeds_the_directory(directory, session: Session) -> None:
    # The committed snapshot has ~290 Nigerian banks.
    assert directory.get(session, "058") is not None
    assert directory.get(session, "058").name == "Guaranty Trust Bank"


@pytest.mark.parametrize(
    ("query", "expected_code"),
    [
        ("GTBank", "058"),          # curated alias
        ("gtb", "058"),             # curated alias
        ("Guaranty Trust Bank", "058"),  # exact name
        ("guaranty-trust-bank", "058"),  # exact slug
        ("058", "058"),             # numeric code
        ("UBA", "033"),             # 3-letter name that looks code-like
        ("Access Bank", "044"),
        ("Zenith Bank", "057"),
    ],
)
def test_resolve_name_exact_hits(directory, session, query, expected_code) -> None:
    matches = directory.resolve_name(session, query)
    assert matches, f"{query!r} should resolve"
    assert matches[0].code == expected_code
    assert matches[0].exact is True


def test_short_name_that_looks_like_a_code_falls_through_to_alias(
    directory, session
) -> None:
    # "UBA" matches the code *shape* (3 alphanumerics) but no bank has
    # that code, so resolution must fall through to the alias map
    # instead of returning an empty list.
    assert directory.resolve_name(session, "UBA")[0].code == "033"


def test_ambiguous_query_returns_a_list_and_is_not_exact(
    directory, session
) -> None:
    matches = directory.resolve_name(session, "microfinance")
    assert len(matches) > 1
    assert all(m.exact is False for m in matches)
    assert directory.resolve_exact(session, "microfinance") is None


def test_unknown_name_returns_no_matches(directory, session) -> None:
    assert directory.resolve_name(session, "ZZZQQQ Not A Bank") == []
    assert directory.resolve_exact(session, "ZZZQQQ Not A Bank") is None


def test_normalize_bank_fields_resolves_a_name(directory, session) -> None:
    code, name = directory.normalize_bank_fields(session, code="GTBank")
    assert code == "058"
    assert name == "Guaranty Trust Bank"


def test_normalize_bank_fields_prefers_code_over_name(directory, session) -> None:
    code, name = directory.normalize_bank_fields(
        session, code="058", name="something else"
    )
    assert (code, name) == ("058", "Guaranty Trust Bank")


def test_normalize_bank_fields_drops_unresolvable_value(directory, session) -> None:
    # An unresolvable value must NOT be kept as a code - that is what
    # used to be forwarded to Paystack as "Unknown bank code".
    code, name = directory.normalize_bank_fields(session, code="ZZZQQQ Not A Bank")
    assert code is None
    assert name == "ZZZQQQ Not A Bank"


def test_is_valid_code(directory, session) -> None:
    assert directory.is_valid_code(session, "058") is True
    assert directory.is_valid_code(session, "999999999") is False
    assert directory.is_valid_code(session, "") is False


async def test_refresh_upserts_from_provider(session, stub_provider) -> None:
    # Bind the directory to THIS stub so `calls` is inspectable; the
    # autouse fixture installs a different instance.
    directory = get_bank_directory(provider=stub_provider)

    count = await directory.refresh(session)

    # The stub returns 4 banks (058, 044, 057, 033).
    assert count == 4
    assert [name for name, _ in stub_provider.calls] == ["list_banks"]
    assert stub_provider.calls[0][1] == {"country": "nigeria"}


# ── test-only banks ────────────────────────────────────────────────

def test_test_bank_is_seeded_outside_production(session, monkeypatch) -> None:
    from sqlmodel import delete

    from app.core.config import settings
    from app.models.bank import Bank

    session.exec(delete(Bank).where(Bank.code == "001"))
    session.commit()

    monkeypatch.setattr(settings, "environment", "development")
    directory = get_bank_directory()
    directory.ensure_loaded(session)

    bank = directory.get(session, "001")
    assert bank is not None
    assert bank.name == "Test Bank"


def test_test_bank_is_not_seeded_in_production(session, monkeypatch) -> None:
    from sqlmodel import delete

    from app.core.config import settings
    from app.models.bank import Bank

    session.exec(delete(Bank).where(Bank.code == "001"))
    session.commit()

    monkeypatch.setattr(settings, "environment", "production")
    directory = get_bank_directory()
    directory.ensure_loaded(session)

    assert directory.get(session, "001") is None
