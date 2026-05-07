# api/authRoute.py
from fastapi import APIRouter, Depends, HTTPException, logger
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.user import User, TelegramLinkCode
from datetime import datetime, timedelta
from pydantic import BaseModel
import hashlib
from app.services.payaza import create_virtual_account
from app.models.transaction import VirtualAccount
from fastapi.logger import logger

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Request bodies ─────────────────────────────────────────────────

class SignupRequest(BaseModel):
    first_name: str
    last_name: str
    phone_number: str
    bvn: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str

class VerifyLinkRequest(BaseModel):
    code: str
    telegram_chat_id: str

# ── Mockup password hashing (good enough for hackathon) ────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


# ── Endpoints ──────────────────────────────────────────────────────

@router.post("/signup")
async def signup(body: SignupRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(User).where(User.email == body.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        phone_number=body.phone_number,
        bvn=body.bvn,
        hashed_password=hash_password(body.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    va_data = {}
    try:
        va_response = await create_virtual_account(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            phone_number=user.phone_number, 
            bvn=user.bvn
            )
        
        va_data = va_response.get("data", {})
        virtual_account = VirtualAccount(
            user_id=user.id,
            account_number=va_data.get("account_number"),
            #bank_code=va_data.get("bank_code"),
            bank_name=va_data.get("bank_name"),
            account_name=f'{user.first_name} {user.last_name}',
            bvn=user.bvn,
            bvn_validated=va_data.get("bvn_validated"),
            account_reference=va_data.get("account_reference")
        )

        session.add(virtual_account)
        session.commit()
    except Exception as e:
        # CRITICAL: Rollback the session to clear the 'PendingRollbackError'
        session.rollback() 
        
        # Clean up the user if the VA creation failed so they can try again
        # Note: Depending on your flow, you might want to keep the user and 
        # just let them retry VA creation later.
        user_to_delete = session.get(User, user.id)
        if user_to_delete:
            session.delete(user_to_delete)
            session.commit()
            
        logger.error(f"Signup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Signup failed: {str(e)}")

    return {"user_id": user.id, "email": user.email, "account": virtual_account.account_number, 'bank_name': virtual_account.bank_name}


@router.post("/login")
def login(body: LoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == body.email)).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return {
        "user_id": user.id,
        "email": user.email,
        "is_telegram_linked": user.is_telegram_linked,
        "telegram_chat_id": user.telegram_chat_id,
    }


@router.post("/link-code")
def generate_link_code(user_id: int, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Invalidate old unused codes
    old_codes = session.exec(
        select(TelegramLinkCode).where(
            TelegramLinkCode.user_id == user_id,
            TelegramLinkCode.is_used == False
        )
    ).all()
    for c in old_codes:
        session.delete(c)

    link_code = TelegramLinkCode(
        user_id=user_id,
        code=TelegramLinkCode.generate_code(),
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    session.add(link_code)
    session.commit()
    session.refresh(link_code)

    return {
        "code": link_code.code,
        "expires_in_minutes": 10,
        "instruction": f"Send this to your Telegram bot: /link {link_code.code}"
    }


@router.post("/verify-link")
def verify_link(body: VerifyLinkRequest, session: Session = Depends(get_session)):
    link_record = session.exec(
        select(TelegramLinkCode).where(TelegramLinkCode.code == body.code)
    ).first()

    if not link_record:
        raise HTTPException(status_code=404, detail="Invalid code")
    if link_record.is_used:
        raise HTTPException(status_code=400, detail="Code already used")
    if datetime.utcnow() > link_record.expires_at:
        raise HTTPException(status_code=400, detail="Code expired")

    existing = session.exec(
        select(User).where(User.telegram_chat_id == body.telegram_chat_id)
    ).first()
    if existing and existing.id != link_record.user_id:
        raise HTTPException(status_code=409, detail="Telegram already linked to another account")

    user = session.get(User, link_record.user_id)
    user.telegram_chat_id = body.telegram_chat_id
    user.is_telegram_linked = True
    link_record.is_used = True

    session.add(user)
    session.add(link_record)
    session.commit()

    return {"status": "linked", "user_id": user.id}
