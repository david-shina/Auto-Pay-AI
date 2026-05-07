from fastapi import APIRouter, Request, HTTPException, Depends
from sqlmodel import Session, select
from core.database import engine, get_session

from models.transaction import Transaction, VirtualAccount
from models.bill import Bill
from models.user import User
from services.payaza import verify_webhook_signature
from services.payout import _notify_user, schedule_recurrence
import logging
import uuid
from services.payaza import trigger_test_collection
from models.transaction import VirtualAccount
import os
from dotenv import load_dotenv
import base64

load_dotenv(dotenv_path='.env')

token = os.getenv('PAYAZA_PUBLIC_KEY')

encoded_token = None
if token:
    encoded_token = base64.b64encode(token.strip().encode('utf-8')).decode('utf-8')
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/payaza")
async def payaza_webhook(request: Request):
    raw_body = await request.body()
    logger.info(f"=== WEBHOOK HIT === Body: {raw_body.decode()}")
        
    signature = request.headers.get("x-payaza-signature", "")

    # Security: Enable this in production to verify requests come from Payaza
    # if not verify_webhook_signature(raw_body, signature):
    #     raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        return {"status": "invalid json"}

    event = payload.get("event")
    data = payload.get("data", {})

    with Session(engine) as session:
        # HANDLING INFLOW: A user's virtual account was credited
        if event == "virtual_account.credit":
            account_number = data.get("account_number")
            # Payaza sometimes sends amount as 'amount' or 'transaction_amount'
            amount = float(data.get("amount") or data.get("transaction_amount") or 0)
            payaza_ref = data.get("transaction_reference")

            # Idempotency: Don't process the same transaction twice
            existing = session.exec(
                select(Transaction).where(Transaction.payaza_reference == payaza_ref)
            ).first()
            if existing:
                return {"status": "ok"}

            # Find the user associated with this account number
            va = session.exec(
                select(VirtualAccount).where(VirtualAccount.account_number == account_number)
            ).first()

            if not va:
                logger.error(f"No account found for number: {account_number}")
                return {"status": "ok"}

            user = session.get(User, va.user_id)
            user.balance += amount

            # Record the transaction
            new_tx = Transaction(
                user_id=user.id,
                type="credit",
                amount=amount,
                status="success",
                payaza_reference=payaza_ref,
                narration="Wallet top-up (Test)"
            )
            
            session.add(new_tx)
            session.add(user)
            session.commit()

            await _notify_user(
                user.telegram_chat_id,
                f"✅ *Wallet Credited*\n₦{amount:,.2f} added. Balance: ₦{user.balance:,.2f}"
            )

    return {"status": "ok"}


import httpx
# @router.post("/payaza/test-credit")
# async def test_credit_webhook(
#     user_id: int,
#     amount: float,
#     session: Session = Depends(get_session)
# ):
#     """
#     Triggers a real Payaza test collection against the user's virtual account.
#     This uses the 'fund_test_virtual_account' mock endpoint.
#     """
#     # 1. Fetch the user's virtual account from the DB
#     va = session.exec(
#         select(VirtualAccount).where(VirtualAccount.user_id == user_id)
#     ).first()

#     if not va:
#         raise HTTPException(
#             status_code=404,
#             detail="No virtual account found for this user."
#         )

#     # 2. Prepare the Payaza Mock Funding Payload
#     # Mapping your DB fields (va) to the Payaza API requirements
#     url = "https://api.payaza.africa/live/merchant-collection/payaza/virtual_account/fund_test_virtual_account"
    
#     payload = {
#         "account_name": va.account_name, # Assumes your model has this
#         "account_number": va.account_number,
#         "initiation_transaction_reference": "",
#         "transaction_amount": str(amount),
#         "currency": "NGN",
#         "source_account_number": "0123456789",
#         "source_account_name": "Test Payer",
#         "source_bank_name": "Test Bank"
#     }

#     headers = {'Content-Type': 'application/json'}

#     # 3. Fire the request
#     try:
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, json=payload, headers=headers)
#             response.raise_for_status()
#             result = response.json()

#         return {
#             "status": "success",
#             "message": "Mock funding triggered. Payaza will now send a webhook to /webhooks/payaza",
#             "payaza_response": result
#         }

#     except httpx.HTTPStatusError as e:
#         logger.error(f"Payaza API Error: {e.response.text}")
#         raise HTTPException(status_code=400, detail=f"Payaza error: {e.response.text}")
#     except Exception as e:
#         logger.error(f"Unexpected error: {str(e)}")
#         raise HTTPException(status_code=500, detail="Internal server error triggering test")




@router.post("/payaza/test-credit")
async def test_credit_webhook(
    user_id: int,
    amount: float,
    session: Session = Depends(get_session)
):
    va = session.exec(
        select(VirtualAccount).where(VirtualAccount.user_id == user_id)
    ).first()

    if not va:
        raise HTTPException(status_code=404, detail="Virtual account not found.")

    url = "https://api.payaza.africa/live/merchant-collection/payaza/virtual_account/fund_test_virtual_account"

    payload = {
        "account_name": va.account_name,
        "account_number": va.account_number,
        "initiation_transaction_reference": '',
        "transaction_amount": str(amount),
        "currency": "NGN",
        "source_account_number": "0123456789",
        "source_account_name": "Mock Payer",
        "source_bank_name": "Test Bank"
    }

    headers = {
        'Content-Type': 'application/json'
    }
    if encoded_token:
        headers['Authorization'] = f'Payaza {encoded_token}'

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        
        payaza_response = response.json()
        logger.info(f"Payaza test credit response: {payaza_response}")

    except Exception as e:
        logger.error(f"Failed to trigger test credit: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    # ── Payaza accepted the request — now do everything the webhook would have done ──

    # 1. Idempotency check — build a deterministic reference from this test call
    #    (Payaza won't give us a real ref here, so we generate one)
    payaza_ref = (
        payaza_response.get("transaction_reference")
        or payaza_response.get("data", {}).get("transaction_reference")
        or f"test_{va.account_number}_{uuid.uuid4().hex[:8]}"
    )

    existing = session.exec(
        select(Transaction).where(Transaction.payaza_reference == payaza_ref)
    ).first()

    if existing:
        return {
            "status": "already_processed",
            "message": "This transaction was already credited.",
            "reference": payaza_ref
        }

    # 2. Load user
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # 3. Credit the wallet
    user.balance += amount

    # 4. Record the transaction
    credit = Transaction(
        user_id=user.id,
        type="credit",
        amount=amount,
        fee=0.0,
        currency=va.currency,
        status="success",
        payaza_reference=payaza_ref,
        narration="Wallet top-up (test credit)",
    )

    session.add(credit)
    session.add(user)
    session.commit()
    session.refresh(user)

    logger.info(f"Wallet credited ₦{amount:,.2f} for user {user_id}. New balance: ₦{user.balance:,.2f}")

    # 5. Send Telegram notification
    await _notify_user(
        user.telegram_chat_id,
        f"✅ *Wallet Credited*\n\n"
        f"₦{amount:,.2f} has been added to your AutoPay wallet.\n"
        f"New balance: ₦{user.balance:,.2f}\n"
        f"Reference: `{payaza_ref}`"
    )

    return {
        "status": "success",
        "message": f"₦{amount:,.2f} credited to wallet successfully.",
        "new_balance": user.balance,
        "reference": payaza_ref,
        "payaza_response": payaza_response
    }
