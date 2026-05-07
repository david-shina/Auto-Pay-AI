from typing import TypedDict, Annotated, Optional

class AgentState(TypedDict):
    bill_id : int
    user_id: int
    telegram_chat_id: str
    currency: str
    user_balance: float
    bill_amount: float
    due_date: str
    decision: Optional[str]
    reasoning: Optional[str]
    is_approved: bool

