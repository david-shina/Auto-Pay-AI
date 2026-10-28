"""Bank directory - a local cache of the provider's supported banks.

Why this exists:
  Paystack's transfer flow speaks in *bank codes* ("058" for GTBank).
  Humans speak in bank *names* ("GTBank", "UBA", "First Bank"). Before
  this table existed, a name typed by the user was written straight into
  `bills.bank_code` and forwarded to `GET /bank/resolve`, which rejected
  it - surfacing as a raw "Unknown bank code" error.

  This table is populated from `GET /bank`, so we can turn a name into
  a code locally (no API round-trip per lookup) and offer a picker
  instead of guessing.

Schema notes (all observed against the live `GET /bank` response):
  * Codes are NOT always numeric - the list contains "C08", "035A",
    "MFB50094", "00zap", up to 9 characters. Treat the code as an
    opaque string.
  * Codes are NOT unique - 6 codes are shared by two different
    institutions (e.g. "100025" is both Kolomoni MFB and KongaPay).
    So `code` cannot be the primary key; we use a surrogate `id` and
    a UNIQUE (country, code, name) to dedupe identical rows.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class Bank(SQLModel, table=True):
    __tablename__ = "banks"
    __table_args__ = (
        UniqueConstraint(
            "country", "code", "name", name="uq_banks_country_code_name"
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )

    # Paystack `code` - the value every other table stores in
    # `bank_code`. Indexed but not unique (see module docstring).
    code: str = Field(sa_column=Column(String(16), nullable=False, index=True))

    # Display name, e.g. "Guaranty Trust Bank".
    name: str = Field(sa_column=Column(String(128), nullable=False, index=True))

    # Paystack `slug`, e.g. "guaranty-trust-bank". Handy for matching
    # what a URL or an LLM might produce.
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, index=True),
    )

    # Paystack `type`: "nuban", "mobile_money", "ghipss", ...
    type: str | None = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
    )

    country: str = Field(
        default="nigeria",
        sa_column=Column(String(32), nullable=False, index=True),
    )

    # Paystack marks delisted banks `active=false` / `is_deleted=true`.
    # We keep them (existing bills may reference them) but exclude them
    # from search results.
    active: bool = Field(default=True, nullable=False)

    # When this row was last confirmed against `GET /bank`.
    synced_at: datetime = Field(default_factory=datetime.now, nullable=False)
