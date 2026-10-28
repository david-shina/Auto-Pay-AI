"""One-off backfill: turn bank *names* in `bills.bank_code` into codes.

Before the bank directory existed, a bank name typed by a user (or
returned by the bill extractor) was written straight into
`bills.bank_code`. Those rows make `execute_payout` fail with
"Unknown bank code", because Paystack's `GET /bank/resolve` only
accepts a real code.

This script walks every bill whose `bank_code` is not a code the
directory knows, tries to resolve it by name, and rewrites
`bank_code` / `bank_name` when the match is unambiguous. Rows it
cannot resolve are reported so they can be fixed by hand.

Dry-run by default. Nothing is written without --apply.

Run with:
  make backfill-banks            # report only
  python -m scripts.backfill_bank_codes --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app` importable when running this file as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from app.core.database import engine
from app.models.bank import Bank
from app.models.bill import Bill
from app.services.banks import get_bank_directory


def _needs_backfill(bill: Bill, known_codes: set[str]) -> bool:
    code = (bill.bank_code or "").strip()
    return bool(code) and code not in known_codes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the resolved codes (default: report only)",
    )
    args = parser.parse_args()

    with Session(engine) as session:
        directory = get_bank_directory()
        directory.ensure_loaded(session)

        # Authoritative set of valid codes, from the directory itself.
        known_codes = {row.code for row in session.exec(select(Bank)).all()}

        bills = session.exec(select(Bill).where(Bill.bank_code.is_not(None))).all()
        candidates = [b for b in bills if _needs_backfill(b, known_codes)]

        print(f"scanned {len(bills)} bills with a bank_code")
        print(f"{len(candidates)} need backfill")

        fixed = 0
        unresolved: list[tuple[int, str, str]] = []

        for bill in candidates:
            raw = (bill.bank_code or "").strip()
            match = directory.resolve_exact(session, raw)
            if match is None:
                unresolved.append((bill.id, raw, bill.bank_name or ""))
                continue

            print(
                f"  bill {bill.id}: {raw!r} -> {match.code} ({match.name})"
            )
            if args.apply:
                bill.bank_code = match.code
                bill.bank_name = match.name
                session.add(bill)
            fixed += 1

        if args.apply and fixed:
            session.commit()

        verb = "fixed" if args.apply else "would fix"
        print(f"\n{verb}: {fixed}")

        if unresolved:
            print(f"unresolved: {len(unresolved)} (needs manual review)")
            for bill_id, raw, name in unresolved:
                print(f"  bill {bill_id}: bank_code={raw!r} bank_name={name!r}")

        if not args.apply and fixed:
            print("\nre-run with --apply to write these changes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
