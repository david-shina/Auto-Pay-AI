"""Baseline migration.

The schema is defined in `schema.sql` at the repository root. This
migration applies that file, so `alembic upgrade head` works on an
empty database.

Every statement in `schema.sql` is idempotent (`CREATE TABLE IF NOT
EXISTS`, `CREATE INDEX IF NOT EXISTS`, ...), so applying it to a
database that already has the schema — e.g. one created by loading
`schema.sql` by hand, or by the Postgres container's initdb hook — is
a no-op. `alembic stamp head` remains valid for those databases too.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-01
"""
from collections.abc import Sequence
from pathlib import Path
from typing import Union

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# migrations/versions/0001_baseline.py -> repository root
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"


def upgrade() -> None:
    """Apply `schema.sql`."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    # `exec_driver_sql` hands the script to the driver untouched, so the
    # multi-statement file (including the dollar-quoted DO block) runs
    # as one batch. `op.execute()` would wrap it in a compiled
    # `text()` construct and reject the multiple statements.
    op.get_bind().exec_driver_sql(schema_sql)


def downgrade() -> None:
    """No-op. To wipe the schema, drop and recreate the database."""
