"""Add provider_customer_code to virtual_accounts.

Revision ID: 0004_va_customer_code
Revises: 0003_provider_order_reference
Create Date: 2026-10-20

Why this migration:
  A bank transfer into a Paystack dedicated virtual account arrives as
  a `charge.success` webhook that carries the gateway's *customer
  code* rather than one of our transaction references. Without
  storing that code we cannot tell which user's wallet the transfer
  belongs to, so the credit was dropped as an orphan.

  `provider_customer_code` lets the webhook handler map an incoming
  transfer back to the user who owns the account.

Note: the revision id must stay within 32 characters - Alembic's
`alembic_version.version_num` column is `VARCHAR(32)`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_va_customer_code"
down_revision: Union[str, None] = "0003_provider_order_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # `schema.sql` already adds this column; skip if it is there.
    if _has_column("virtual_accounts", "provider_customer_code"):
        return
    op.add_column(
        "virtual_accounts",
        sa.Column("provider_customer_code", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "idx_virtual_accounts_provider_customer_code",
        "virtual_accounts",
        ["provider_customer_code"],
    )


def downgrade() -> None:
    if not _has_column("virtual_accounts", "provider_customer_code"):
        return
    op.drop_index(
        "idx_virtual_accounts_provider_customer_code",
        table_name="virtual_accounts",
    )
    op.drop_column("virtual_accounts", "provider_customer_code")
