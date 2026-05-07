from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class VirtualAccount(SQLModel, table=True):
    __tablename__ = "virtual_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", unique=True, index=True)
    account_number: str = Field(unique=True, index=True)
    currency: str = Field(default="NGN")
    account_name: str
    bank_name: str
    bvn: str
    bvn_validated: bool = Field(default=False)
    account_reference: str = Field(unique=True)  # Payaza's reference
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    bill_id: Optional[int] = Field(default=None, foreign_key="bill.id")

    # What kind of transaction
    type: str  # "credit" (inflow) | "debit" (payout)
    amount: float
    fee: float = Field(default=0.0)
    currency: str = Field(default="NGN")

    # Tracking
    status: str = Field(default="pending")
    # pending → processing → success | failed
    
    payaza_reference: Optional[str] = Field(default=None, unique=True, index=True)
    retry_count: int = Field(default=0)
    failure_reason: Optional[str] = None

    narration: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)