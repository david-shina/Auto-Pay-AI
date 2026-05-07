from .state import AgentState
import instructor
import asyncio
from pydantic import BaseModel, Field
from groq import Groq
from datetime import datetime
from dateutil import parser as dateparser

class DecisionResponse(BaseModel):
    decision: str = Field(description="Must be exactly 'pay_now', 'schedule', or 'hold'")
    reasoning: str = Field(description="Brief explanation of why this choice was made")

async def make_decision_node(state: AgentState):
    client = instructor.from_groq(Groq())

    # Calculate days until due HERE so the LLM doesn't have to
    today = datetime.utcnow()
    due_date = dateparser.parse(str(state["due_date"]))
    days_until_due = (due_date - today).days if due_date else 0

    prompt = f"""
    You are a cash-flow manager. Make a payment decision based on these facts.

    Today's date: {today.strftime("%Y-%m-%d")}
    Due date: {state['due_date']}
    Days until due: {days_until_due}

    User balance: ₦{state['user_balance']:,.2f}
    Bill amount:  ₦{state['bill_amount']:,.2f}
    Can afford:   {"YES" if state['user_balance'] >= state['bill_amount'] else "NO"}

    Decision rules (apply in this exact order):
    1. If balance < bill amount → decision MUST be 'hold'
    2. If balance >= bill amount AND days_until_due <= 3 → decision MUST be 'pay_now'
    3. If balance >= bill amount AND days_until_due > 3  → decision MUST be 'schedule'

    You must return one of exactly: 'pay_now', 'schedule', 'hold'
    """

    response = await asyncio.to_thread(
        client.chat.completions.create,
        model="openai/gpt-oss-20b",
        response_model=DecisionResponse,
        messages=[{"role": "user", "content": prompt}],
    )

    return {
        "decision": response.decision,
        "reasoning": response.reasoning,
    }