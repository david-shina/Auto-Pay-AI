"""Bill DTOs — upload, list, get, pay, cancel."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BillExtractionResult(BaseModel):
    """What the loader + LLM extracted from the upload.

    `due_date` is intentionally a plain `str` (not `datetime`). LLMs are
    unreliable at emitting format-strict dates — `"2026-03-15"`,
    `"15/03/2026"`, `"March 15 2026"`, `"in 2 weeks"`, and sometimes
    `null` all show up in practice. The downstream parser in
    `app.services.date_parser.parse_bill_due_date` accepts all of these
    gracefully. Don't switch this back to `datetime` or `Union` — every
    Pydantic v2 strict-mode validation in the image loader will explode.
    """

    vendor_name: str
    amount: float
    currency: str = "NGN"
    due_date: str | None = None
    account_number: str | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw_text: str | None = None


class BillCreateRequest(BaseModel):
    """When the caller already knows the bill details (no upload)."""

    vendor_name: str = Field(min_length=1, max_length=255)
    amount: float = Field(gt=0)
    due_date: datetime
    account_number: str | None = Field(default=None, max_length=32)
    bank_code: str | None = Field(default=None, max_length=16)
    bank_name: str | None = Field(default=None, max_length=128)


class BillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    vendor_name: str
    account_number: str | None
    bank_code: str | None
    bank_name: str | None
    amount: Decimal
    currency: str
    due_date: datetime
    status: str
    is_recurring: bool
    retry_count: int
    created_at: datetime


class BillActionResponse(BaseModel):
    """Returned by /pay, /cancel, /upload — short, opinionated envelope."""

    bill: BillResponse
    message: str
    decision: str | None = None  # e.g. "pay_now" | "hold" | "schedule"
    decision_reason: str | None = None
