"""Conftest for unit tests that need DB access.

The DB-backed unit tests in `test_telegram_handlers.py` reuse the
integration conftest's `session` fixture (Postgres + JSONB) so we
don't have to maintain a separate SQLite schema. This conftest
exists only to make the integration fixtures visible from the
`tests/unit/` directory.

Note: the autouse fixture is deliberately *not* imported here. It is
autouse in `tests/integration/`, and re-exporting it would make every
unit test truncate the database - most unit tests never touch it, and
the truncate dominated the runtime of the suite. The `session` and
`client` fixtures depend on `clean_db` explicitly, so the tests that
do use the database still start from a clean slate.
"""
from tests.integration.conftest import (  # noqa: F401
    clean_db,
    client,
    session,
    stub_provider,
)
