import csv
import os
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Path to the CSV file — put it in your project root or adjust as needed
BANK_CODES_FILE = "bank_codes.csv"
BANK_CODES_FILE1 = "Book1.csv"

@lru_cache(maxsize=1)
def _load_banks() -> dict[str, str]:
    """
    Loads the CSV once and caches it for the lifetime of the app.
    Returns { "BANK NAME UPPERCASED": "bank_code" }
    """
    banks = {}
    with open(BANK_CODES_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["Bank Code"].strip()
            name = row["Bank Name"].strip().upper()
            banks[name] = code
    logger.info(f"Loaded {len(banks)} banks from {BANK_CODES_FILE}")
    return banks

@lru_cache(maxsize=1)
def _load_banks1() -> dict[str, str]:
    banks = {}
    with open(BANK_CODES_FILE1, newline="", encoding="utf-8") as f:
        next(f)  # skip header line
        for line in f:
            # Strip outer quotes and whitespace, then split on first comma
            line = line.strip().strip('"')
            if not line:
                continue
            parts = line.split(",", 1)  # split on first comma only
            if len(parts) != 2:
                continue
            code = parts[0].strip()
            name = parts[1].strip().upper()
            banks[name] = code

    logger.info(f"Loaded {len(banks)} banks from {BANK_CODES_FILE1}")
    print()
    return banks


def get_bank_code1(bank_name: str, threshold: int = 60) -> Optional[str]:
    """
    Looks up a bank code by name.

    1. Tries an exact match first (case-insensitive).
    2. Falls back to fuzzy matching if no exact match is found.
    3. Returns None if no match is above the threshold.

    Args:
        bank_name:  The bank name as extracted by the AI (e.g. "Access Bank", "GTBank")
        threshold:  Minimum similarity score (0-100) to accept a fuzzy match.
                    60 is a safe default — lower it if you're missing valid banks,
                    raise it if you're getting wrong matches.

    Returns:
        The bank code string (e.g. "000014") or None if not found.
    """
    banks = _load_banks1()
    query = bank_name.strip().upper()

    # ── 1. Exact match ─────────────────────────────────────────────
    if query in banks:
        logger.info(f"Exact match: '{bank_name}' → {banks[query]}")
        return banks[query]

    # ── 2. Fuzzy match ─────────────────────────────────────────────
    best_score = 0
    best_code = None
    best_name = None

    for name, code in banks.items():
        score = SequenceMatcher(None, query, name).ratio() * 100
        if score > best_score:
            best_score = score
            best_code = code
            best_name = name

    if best_score >= threshold:
        logger.info(
            f"Fuzzy match: '{bank_name}' → '{best_name}' "
            f"({best_score:.1f}%) → {best_code}"
        )
        return best_code

    logger.warning(
        f"No match found for '{bank_name}' "
        f"(best was '{best_name}' at {best_score:.1f}%)"
    )
    return None


def get_bank_code(bank_name: str, threshold: int = 60) -> Optional[str]:
    """
    Looks up a bank code by name.

    1. Tries an exact match first (case-insensitive).
    2. Falls back to fuzzy matching if no exact match is found.
    3. Returns None if no match is above the threshold.

    Args:
        bank_name:  The bank name as extracted by the AI (e.g. "Access Bank", "GTBank")
        threshold:  Minimum similarity score (0-100) to accept a fuzzy match.
                    60 is a safe default — lower it if you're missing valid banks,
                    raise it if you're getting wrong matches.

    Returns:
        The bank code string (e.g. "000014") or None if not found.
    """
    banks = _load_banks()
    query = bank_name.strip().upper()

    # ── 1. Exact match ─────────────────────────────────────────────
    if query in banks:
        logger.info(f"Exact match: '{bank_name}' → {banks[query]}")
        return banks[query]

    # ── 2. Fuzzy match ─────────────────────────────────────────────
    best_score = 0
    best_code = None
    best_name = None

    for name, code in banks.items():
        score = SequenceMatcher(None, query, name).ratio() * 100
        if score > best_score:
            best_score = score
            best_code = code
            best_name = name

    if best_score >= threshold:
        logger.info(
            f"Fuzzy match: '{bank_name}' → '{best_name}' "
            f"({best_score:.1f}%) → {best_code}"
        )
        return best_code

    logger.warning(
        f"No match found for '{bank_name}' "
        f"(best was '{best_name}' at {best_score:.1f}%)"
    )
    return None


def get_bank_name(bank_code: str) -> Optional[str]:
    """Reverse lookup — get bank name from code."""
    banks = _load_banks()
    for name, code in banks.items():
        if code == bank_code.strip():
            return name.title()  # returns "Access Bank" not "ACCESS BANK"
    return None


def list_all_banks() -> list[dict]:
    """Returns all banks as a list of {code, name} dicts. Useful for dropdowns."""
    banks = _load_banks()
    return [
        {"code": code, "name": name.title()}
        for name, code in sorted(banks.items(), key=lambda x: x[0])
    ]
