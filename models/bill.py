from pydantic import BaseModel, Field
from sqlmodel import SQLModel, Field as SQLField
from typing import Optional
from datetime import datetime, date
import secrets

# --- 1. AI EXTRACTION SCHEMA ---
# This is what we pass to the LLM via the Instructor library
class BillExtraction(BaseModel):
    vendor_name: str = Field(description="The name of the company issuing the bill")
    amount: float = Field(description="The total amount to be paid")
    currency: str = Field(default="NGN", description="The currency code (e.g., NGN, USD)")
    due_date: str = Field(description="The due date as found on the bill (e.g., YYYY-MM-DD)")
    account_number: Optional[str] = Field(None, description="The bank account number for payment")
    bank_code: Optional[str] = Field(None, description="The name of the bank for the transfer")
    payment_reference: Optional[str] = Field(None, description="Any unique reference number")

# --- 2. DATABASE TABLE ---
# This is what actually goes into PostgreSQL
class Bill(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    user_id: int = SQLField(foreign_key="users.id")
    vendor_name: str
    amount: float
    currency: str = "NGN"
    due_date: datetime
    account_number: Optional[str]
    bank_code: Optional[str]
    status: str = "pending"  # pending, scheduled, paid, failed
    created_at: datetime = SQLField(default_factory=datetime.utcnow)

    is_recurring: bool = SQLField(default=False)
    recurrence_interval: Optional[str] = None  # "monthly" | "weekly"
    next_recurrence_date: Optional[datetime] = None

    retry_count: int = SQLField(default=0)
    max_retries: int = SQLField(default=3)



