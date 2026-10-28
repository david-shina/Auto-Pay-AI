"""Bank directory DTOs - search + resolve responses for the bank picker."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BankOption(BaseModel):
    """One selectable bank in the picker."""

    code: str
    name: str
    slug: str | None = None
    # True when this came from an exact code / alias / name / slug hit,
    # i.e. it is safe to select without further confirmation.
    exact: bool = False
    score: int | None = None


class BankResolveResponse(BaseModel):
    """Result of resolving a free-text bank name.

    `exact` is set only when there is a single unambiguous answer.
    Otherwise the caller must show `matches` and let the user choose.
    """

    query: str
    exact: BankOption | None = None
    matches: list[BankOption] = Field(default_factory=list)
