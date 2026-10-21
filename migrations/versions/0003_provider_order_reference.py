"""Add provider_order_reference column to transactions.

Revision ID: 0003_provider_order_reference
Revises: 0002_webhook_events
Create Date: 2026-07-06

Why this migration:
  Paystack echoes our reference back, but Nomba generates its own
  `orderReference` (a UUID) for each Checkout. Our `start_topup`
  function now stores both:
    * `provider_reference`         - our internal reference, returned
                                      to the user, used for webhook
                                      lookups
    * `provider_order_reference`   - the provider's reference, used
                                      as the query parameter when
                                      calling the provider's
                                      transaction-status API

  This column is nullable, so existing rows continue to work. The
  verify endpoint falls back to `provider_reference` when this is
  NULL (i.e. for topups created before this migration).

Note: the revision id must stay within 32 characters - Alembic's
`alembic_version.version_num` column is `VARCHAR(32)`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_provider_order_reference"
down_revision: str | None = "0002_webhook_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `schema.sql` already adds this column, so a database built from
    # the schema file is already up to date. Skip instead of failing.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("transactions") and "provider_order_reference" in {
        col["name"] for col in inspector.get_columns("transactions")
    }:
        return
    op.add_column(
        "transactions",
        sa.Column("provider_order_reference", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_transactions_provider_order_reference",
        "transactions",
        ["provider_order_reference"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("transactions"):
        return
    if "provider_order_reference" not in {
        col["name"] for col in inspector.get_columns("transactions")
    }:
        return
    op.drop_index(
        "ix_transactions_provider_order_reference", table_name="transactions"
    )
    op.drop_column("transactions", "provider_order_reference")
