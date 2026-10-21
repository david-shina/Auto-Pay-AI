"""Transaction DTOs — wallet history."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    bill_id: int | None
    type: str
    amount: Decimal
    fee: Decimal
    currency: str
    status: str
    provider: str
    provider_reference: str | None
    narration: str | None
    failure_reason: str | None
    created_at: datetime
