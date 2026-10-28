"""Create the banks directory table.

Revision ID: 0005_banks
Revises: 0004_va_customer_code
Create Date: 2026-10-21

Why this migration:
  Transfer calls need a numeric-ish bank *code* ("058"), but users
  supply a bank *name* ("GTBank"). Nothing converted one to the other,
  so the name was written into `bills.bank_code` and rejected by
  `GET /bank/resolve` - the user saw a raw "Unknown bank code" error.

  This table caches Paystack's `GET /bank` list so a name can be
  resolved locally. Two quirks of the live data are baked into the
  schema:
    * `code` is not unique (6 codes are shared by two institutions),
      hence the surrogate `id` and UNIQUE (country, code, name);
    * `code` is not always numeric ("C08", "MFB50094"), so it is TEXT.

Note: the revision id must stay within 32 characters - Alembic's
`alembic_version.version_num` column is `VARCHAR(32)`.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_banks"
down_revision: str | None = "0004_va_customer_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    # `schema.sql` already creates this table; skip if it is there.
    if _has_table("banks"):
        return

    op.create_table(
        "banks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=True),
        sa.Column("country", sa.String(length=32), nullable=False, server_default="nigeria"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "country", "code", "name", name="uq_banks_country_code_name"
        ),
    )
    op.create_index("idx_banks_code", "banks", ["code"])
    op.create_index("idx_banks_name", "banks", ["name"])
    op.create_index("idx_banks_country", "banks", ["country"])


def downgrade() -> None:
    if not _has_table("banks"):
        return
    op.drop_index("idx_banks_country", table_name="banks")
    op.drop_index("idx_banks_name", table_name="banks")
    op.drop_index("idx_banks_code", table_name="banks")
    op.drop_table("banks")
