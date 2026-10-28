"""Add provider_transfer_code to transactions.

Revision ID: 0006_transfer_code
Revises: 0005_banks
Create Date: 2026-10-28

Why this migration:
  Paystack holds a transfer pending OTP approval when transfer OTP is
  enabled on the business: `POST /transfer` returns 200 with
  `status: "otp"` and a `transfer_code` ("TRF_..."). Until now we took
  that as success, debited the wallet and left the bill in
  `processing` forever, because the transfer was never finalized and no
  webhook ever arrived.

  We store the `transfer_code` so the transfer can be finalized with
  `POST /transfer/finalize_transfer`, and so the reconciliation job can
  tell "awaiting OTP" apart from "in flight" (a non-null value on a
  `processing` debit means awaiting OTP).

  Deliberately no new status value: `transactions.status` has a CHECK
  constraint owned by schema.sql, and adding a status would mean
  rewriting that constraint in two places for no extra information.

Note: the revision id must stay within 32 characters - Alembic's
`alembic_version.version_num` column is `VARCHAR(32)`.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_transfer_code"
down_revision: str | None = "0005_banks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # `schema.sql` already adds this column; skip if it is there.
    if _has_column("transactions", "provider_transfer_code"):
        return
    op.add_column(
        "transactions",
        sa.Column("provider_transfer_code", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_transactions_provider_transfer_code",
        "transactions",
        ["provider_transfer_code"],
    )


def downgrade() -> None:
    if not _has_column("transactions", "provider_transfer_code"):
        return
    op.drop_index(
        "idx_transactions_provider_transfer_code",
        table_name="transactions",
    )
    op.drop_column("transactions", "provider_transfer_code")
