"""Refresh the local bank directory and regenerate the committed snapshot.

Two steps, on purpose:
  1. `refresh()` pulls `GET /bank` into the `banks` table (what the
     running app does daily via the scheduler).
  2. `export_snapshot()` writes the table to
     `app/services/data/banks_ng.json` - the bootstrap file used when a
     fresh database has no rows yet.

Run this when you want to commit an updated snapshot:

  python -m scripts.refresh_banks
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make `app` importable when running this file as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import session_scope
from app.services.banks import SNAPSHOT_PATH, get_bank_directory


async def _run() -> None:
    with session_scope() as session:
        directory = get_bank_directory()
        fetched = await directory.refresh(session)
        written = directory.export_snapshot(session)

    print(f"fetched {fetched} banks from the provider")
    print(f"wrote {written} banks to {SNAPSHOT_PATH}")


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
