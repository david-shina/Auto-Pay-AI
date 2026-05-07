import httpx
import os
import hashlib
import hmac
import logging
from dotenv import load_dotenv
import base64
import requests

from services.banks import get_bank_code

load_dotenv(dotenv_path='.env')
logger = logging.getLogger(__name__)


PAYAZA_API_KEY = os.getenv("PAYAZA_PUBLIC_KEY")
encoded_token = base64.b64encode(PAYAZA_API_KEY.strip().encode('utf-8')).decode('utf-8')
PAYAZA_WEBHOOK_SECRET = os.getenv("PAYAZA_WEBHOOK_SECRET")


HEADERS = {
    "Authorization": f"Payaza {encoded_token}",
    'X-TenantID': 'test',
    "Content-Type": "application/json",
    "accept": "application/json",
}

async def create_virtual_account(
    user_id: int,
    email: str,
    first_name: str,
    last_name: str,
    phone_number: str,
    bvn: str
) -> dict:
    
    """Called once at signup. Returns account details to store in DB."""
    payload = {
        "account_name": f"{first_name} {last_name}",
        "account_reference": f"autopay_users_{user_id}",
        'account_type': "Static",
        "bank_code": "140",
        "bvn": bvn,
        "bvn_validated": True,
        "customer_first_name": f'{first_name}',
        "customer_last_name": f'{last_name}',
        "customer_email": f'{email}',
        "customer_phone_number": f'{phone_number}'   
    }


    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.payaza.africa/live/merchant-collection/merchant/virtual_account/generate_virtual_account/",
            headers=HEADERS,
            json=payload,
            timeout=30
        )

    if response.status_code not in (200, 201):
        logger.error(f"Virtual account creation failed: {response.text}")
        print(response.status_code)
        raise Exception(f"Payaza error: {response.text}")

    return response.json() 


async def verify_account_name(account_number: str, bank_name: str) -> dict:
    """
    Verify an account before paying.
    Returns { account_name, account_number, bank_name }
    """
    bank_code = get_bank_code(bank_name)
    if not bank_code:
        raise ValueError(f"Invalid bank name: {bank_name}")

    payload = {
        "currency": "NGN",
        "bank_code": bank_code, 
        "account_number": account_number
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.payaza.africa/live/payaza-account/api/v1/mainaccounts/merchant/provider/enquiry",
            headers=HEADERS,
            json=payload,
            timeout=30
        )

    if response.status_code != 200:
        raise Exception(f"Account enquiry failed: {response.text}")

    return response.json()


async def execute_payout(
    transaction_reference: str,
    amount: float,
    account_number: str,
    bank_code: str,
    account_name: str,
    narration: str,
    sender_name: str,
    sender_phone: str,
    sender_address: str
) -> dict:
    """
    Initiates a payout. Returns Payaza's response.
    NOTE: This is async — settlement confirmed via webhook.
    """
    payload = {
            "transaction_type": "nuban",
            "service_payload": {
                "payout_amount": amount,
                "transaction_pin": 123456,
                "account_reference": "1010149482", # Come back
                "country": "NGA",
                "currency": "NGN",
                "payout_beneficiaries": [
                    {
                        "credit_amount": amount,
                        "account_name": account_name,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "narration": narration,
                        "transaction_reference": transaction_reference,
                        "sender": {
                            "sender_name": sender_name,
                            "sender_phone_number": sender_phone,
                            "sender_address": sender_address
                        }
                    }
                ]
            }
        }


    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.payaza.africa/live/payout-receptor/payout",
            headers=HEADERS,
            json=payload,
            timeout=30
        )

    if response.status_code not in (200, 201):
        logger.error(f"Payout failed: {response.text}")
        raise Exception(f"Payout failed: {response.text}")

    return response.json()


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify the request genuinely came from Payaza.
    Call this BEFORE processing any webhook payload.
    """
    if not PAYAZA_WEBHOOK_SECRET:
        logger.warning("PAYAZA_WEBHOOK_SECRET not set — skipping verification")
        return True  # Remove this in production

    expected = hmac.new(
        PAYAZA_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


def names_match(extracted_name: str, verified_name: str, threshold: int = 60) -> bool:
    """
    Fuzzy match between AI-extracted vendor name and bank-verified name.
    Returns True if similarity is above threshold.
    """
    from difflib import SequenceMatcher
    a = extracted_name.lower().strip()
    b = verified_name.lower().strip()
    ratio = SequenceMatcher(None, a, b).ratio() * 100
    logger.info(f"Name match: '{a}' vs '{b}' = {ratio:.1f}%")
    return ratio >= threshold


async def trigger_test_collection(
    account_reference: str,
    amount: float,
    country_code: str = "NG"
) -> dict:
    """
    Calls Payaza's test collection endpoint to simulate a virtual account credit.
    This triggers a real webhook back to your server.
    Only works in test/sandbox mode.
    """
    payload = {
        "transaction_reference": account_reference,
        "amount": amount,
        "country_code": country_code,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.payaza.africa/live/subsidiary/funding/v1/process-collection",
            headers={
                **HEADERS,
                "X-TenantID": "test",
                "X-ProductID": "app",
            },
            json=payload,
            timeout=30
        )

    if response.status_code not in (200, 201):
        logger.error(f"Test collection failed: {response.text}")
        raise Exception(f"Test collection failed: {response.text}")

    logger.info(f"Test collection triggered for ref {account_reference}: {response.text}")
    return response.json()
