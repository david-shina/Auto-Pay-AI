"""Audit log — every state-changing event appends a row.

Rows are inserted in the same transaction as the business write so they
atomically commit/rollback together (see app.services.audit).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    actor: str = Field(max_length=32, nullable=False, index=True)
    event_type: str = Field(max_length=64, nullable=False, index=True)
    entity_type: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    entity_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    before_state: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    after_state: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    # NOTE: 'metadata' is reserved by SQLAlchemy Declarative, so we name the
    # attribute `event_metadata` while the column stays `metadata` in Postgres.
    event_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSONB, nullable=True),
    )
    ip_address: str | None = Field(
        default=None,
        sa_column=Column(INET, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default="NOW()"),
    )
