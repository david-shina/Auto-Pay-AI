from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
import uuid
import secrets

class User(SQLModel, table=True):

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    email: str = Field(unique=True, index=True)
    phone_number: str = Field(unique=True, index=True)
    bvn: str = Field(unique=True, index=True)
    hashed_password: str
    telegram_chat_id: Optional[str] = Field(default=None, unique=True, index=True)
    is_telegram_linked: bool = Field(default=False)
    balance: float = Field(default=0.0)
    currency: str = Field(default="NGN")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    address: Optional[str] = Field(default='')

class TelegramLinkCode(SQLModel, table=True):

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    code: str = Field(unique=True, index=True)
    expires_at: datetime
    is_used: bool = Field(default=False)

    @staticmethod
    def generate_code() -> str:
        # 6-char alphanumeric, uppercase, easy to read/type
        return secrets.token_hex(3).upper()
