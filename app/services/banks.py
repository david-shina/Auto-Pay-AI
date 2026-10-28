"""Local bank directory: turn a bank *name* into a Paystack bank *code*.

Context: every transfer call needs a numeric bank code ("058"), but
users think and type in names ("GTBank", "UBA"). Before this module
existed, a name went straight into `bills.bank_code` and then into
`GET /bank/resolve`, which rejected it - surfacing to the user as a
raw "Unknown bank code" error.

Resolution strategy, in order:
  1. numeric input            -> treated as a code, validated against the table
  2. curated alias            -> "gtb" -> "058" (only if that code exists)
  3. exact normalised name    -> "guaranty trust bank" matches the row name
  4. exact slug match         -> "guaranty-trust-bank"
  5. fuzzy ranked fallback    -> rapidfuzz token_set_ratio, top N

Steps 1-4 return a single `exact` match. Step 5 returns a ranked list
and the caller MUST make the user choose: Nigerian bank names are
genuinely ambiguous ("Access Bank" vs "Access Bank (Diamond)", "First
Bank" vs "First Bank of Nigeria"), and silently picking one can send
money to the wrong bank.

The bank list itself lives in the `banks` table, populated from
`GET /bank` by `refresh()` and bootstrapped from the committed JSON
snapshot in `data/banks_ng.json` so a cold deploy still works.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from rapidfuzz import fuzz
from sqlmodel import Session, select

from app.models.bank import Bank
from app.services.payments.base import BankInfo, PaymentProvider

logger = logging.getLogger(__name__)

SNAPSHOT_PATH: Final[Path] = Path(__file__).with_name("data") / "banks_ng.json"

# Fuzzy score below which we drop a candidate entirely. Deliberately
# permissive - the user picks from the list, so a slightly noisy list is
# better than "no match".
_MIN_SCORE: Final[int] = 60
_MAX_MATCHES: Final[int] = 8

# Acronyms and short forms people actually type on bills. These are
# only trusted when the target code is present in the `banks` table, so
# a stale entry degrades to fuzzy matching instead of pointing at a
# bank that no longer exists.
_ALIASES: Final[dict[str, str]] = {
    # Tier 1 commercial banks
    "gtbank": "058",
    "gtb": "058",
    "guaranty trust": "058",
    "access": "044",
    "access bank": "044",
    "zenith": "057",
    "zenith bank": "057",
    "uba": "033",
    "united bank for africa": "033",
    "first bank": "011",
    "firstbank": "011",
    "first bank of nigeria": "011",
    "union bank": "032",
    "fidelity": "070",
    "fidelity bank": "070",
    "stanbic": "221",
    "stanbic ibtc": "221",
    "ibtc": "221",
    "sterling": "232",
    "wema": "035",
    "polaris": "076",
    "ecobank": "050",
    "eco bank": "050",
    "fcmb": "214",
    "first city monument bank": "214",
    "keystone": "082",
    "providus": "101",
    "citibank": "023",
    "unity bank": "215",
    "suntrust": "100",
    "jaiz": "301",
    # Digital / microfinance banks
    "kuda": "50211",
    "kuda bank": "50211",
    "opay": "999992",
    "paycom": "999992",
    "palmpay": "999991",
    "palm pay": "999991",
    "moniepoint": "50515",
    "monie point": "50515",
}

# Noise words that Nigerian bank names carry inconsistently. Stripped
# from both sides before comparison so "Guaranty Trust Bank Plc"
# normalises the same as "guaranty trust bank".
_NOISE: Final[frozenset[str]] = frozenset(
    {"bank", "plc", "ltd", "limited", "nigeria", "nig", "the", "of", "and"}
)


@dataclass(frozen=True)
class BankMatch:
    """One candidate bank for a user-supplied name."""

    code: str
    name: str
    score: int
    exact: bool


def _normalize(text: str) -> str:
    """Casefold, de-accent, strip punctuation + noise words."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.casefold().replace("&", " and ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    tokens = [tok for tok in folded.split() if tok and tok not in _NOISE]
    # If stripping noise removed everything ("Bank"), keep the raw tokens.
    if not tokens:
        tokens = [tok for tok in folded.split() if tok]
    return " ".join(tokens)


def _is_bank_code(text: str) -> bool:
    # Codes are usually 3-6 digits, but Paystack also issues alphanumeric
    # codes ("C08", "035A", "MFB50094"). Existence in the `banks` table
    # is the real validation - this is only a cheap shape check.
    return bool(re.fullmatch(r"[A-Za-z0-9]{3,9}", text.strip()))


class BankDirectory:
    """Read/refresh access to the local `banks` table.

    Stateless apart from the injected provider, so it is cheap to build
    per request. All reads go through the session the caller passes in.
    """

    def __init__(self, provider: PaymentProvider) -> None:
        self._provider = provider

    # ── Population ─────────────────────────────────────────────────

    async def refresh(self, session: Session) -> int:
        """Pull `GET /bank` and upsert into `banks`. Returns row count."""
        infos = await self._provider.list_banks(country="nigeria")
        if not infos:
            logger.warning("bank refresh returned no rows; keeping existing table")
            return 0

        now = datetime.now()
        # Rows are keyed by (country, code, name): the provider reuses a
        # few codes across different institutions, so the code alone is
        # not enough to identify a row.
        existing = {
            (b.country, b.code, b.name): b
            for b in session.exec(select(Bank)).all()
        }
        seen: set[tuple[str, str, str]] = set()

        for info in infos:
            key = (info.country, info.code, info.name)
            if key in seen:
                continue
            seen.add(key)

            row = existing.get(key)
            if row is None:
                session.add(self._to_row(info, now))
                continue
            row.slug = info.slug
            row.type = info.type
            row.active = info.active
            row.synced_at = now

        session.commit()
        logger.info("bank refresh: %d banks synced", len(seen))
        return len(seen)

    def export_snapshot(self, session: Session) -> int:
        """Write the current table to the committed snapshot file.

        Deliberately separate from `refresh()`: refresh runs at runtime
        (container filesystem, ephemeral) and must never mutate a file
        in the source tree - a test that triggered a refresh would
        otherwise overwrite the committed snapshot. Use
        `scripts/refresh_banks.py` to regenerate the snapshot on purpose.
        """
        rows = session.exec(select(Bank).order_by(Bank.name)).all()
        infos = [
            BankInfo(
                code=r.code,
                name=r.name,
                slug=r.slug,
                type=r.type,
                country=r.country,
                active=r.active,
            )
            for r in rows
        ]
        self._write_snapshot(infos)
        return len(infos)

    def ensure_loaded(self, session: Session) -> None:
        """Seed the table from the committed snapshot if it is empty."""
        if session.exec(select(Bank).limit(1)).first() is not None:
            return

        snapshot = self._read_snapshot()
        if not snapshot:
            logger.warning(
                "banks table is empty and no snapshot is available; "
                "run a refresh before accepting bank names"
            )
            return

        now = datetime.now()
        seen: set[tuple[str, str, str]] = set()
        for info in snapshot:
            key = (info.country, info.code, info.name)
            if key in seen:
                continue
            seen.add(key)
            session.add(self._to_row(info, now))
        session.commit()
        logger.info("banks table seeded from snapshot (%d rows)", len(seen))

    @staticmethod
    def _to_row(info: BankInfo, synced_at: datetime) -> Bank:
        return Bank(
            code=info.code,
            name=info.name,
            slug=info.slug,
            type=info.type,
            country=info.country,
            active=info.active,
            synced_at=synced_at,
        )

    # ── Lookups ────────────────────────────────────────────────────

    def get(self, session: Session, code: str) -> Bank | None:
        code = (code or "").strip()
        if not code:
            return None
        # A handful of codes map to more than one institution; any one of
        # them proves the code is valid, so return the first by name.
        return session.exec(
            select(Bank).where(Bank.code == code).order_by(Bank.name)
        ).first()

    def is_valid_code(self, session: Session, code: str) -> bool:
        """True iff `code` is a known bank code (any active state)."""
        return _is_bank_code(code) and self.get(session, code) is not None

    def resolve_exact(self, session: Session, text: str) -> BankMatch | None:
        """Return the match only when it is unambiguous, else None.

        "Unambiguous" means exactly one candidate and it came from an
        exact code / alias / name / slug hit. Fuzzy candidates are never
        auto-selected - paying the wrong bank is irreversible.
        """
        matches = self.resolve_name(session, text)
        if len(matches) == 1 and matches[0].exact:
            return matches[0]
        return None

    def normalize_bank_fields(
        self,
        session: Session,
        *,
        code: str | None = None,
        name: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Turn whatever the caller supplied into `(code, name)`.

        Never raises and never guesses. If the value cannot be resolved
        unambiguously it returns `(None, <raw text>)`, so the caller can
        keep the bill but prompt the user to pick the bank before paying.
        """
        raw = (code or "").strip() or (name or "").strip()
        if not raw:
            return (None, None)

        match = self.resolve_exact(session, raw)
        if match is not None:
            return (match.code, match.name)

        # Keep the human-readable text, drop the unusable code so an
        # invalid value can never reach the transfer call.
        return (None, (name or "").strip() or raw)

    def resolve_name(self, session: Session, text: str) -> list[BankMatch]:
        """Rank the banks matching `text`.

        Returns at most `_MAX_MATCHES`, best first. A single result with
        `exact=True` means the caller may use it without asking the user.
        """
        query = (text or "").strip()
        if not query:
            return []

        banks = session.exec(select(Bank).where(Bank.active.is_(True))).all()

        # 1. The user typed a code. Codes are opaque strings, and short
        #    names ("UBA", "GTBank") also have a code-like shape, so only
        #    accept this branch when a row actually carries that code -
        #    otherwise fall through to name/alias matching.
        for bank in banks:
            if bank.code == query:
                return [BankMatch(bank.code, bank.name, 100, True)]

        normalized = _normalize(query)

        # 2. Curated alias.
        alias_code = _ALIASES.get(normalized)
        if alias_code:
            for bank in banks:
                if bank.code == alias_code:
                    return [BankMatch(bank.code, bank.name, 100, True)]

        # 3/4. Exact name or slug.
        for bank in banks:
            if _normalize(bank.name) == normalized:
                return [BankMatch(bank.code, bank.name, 100, True)]
            if bank.slug and _normalize(bank.slug) == normalized:
                return [BankMatch(bank.code, bank.name, 100, True)]

        # 5. Ranked fuzzy fallback - never auto-selected by callers.
        # `token_set_ratio` is order-insensitive and rewards subset
        # overlap ("microfinance" vs "Shepherd Trust Microfinance"),
        # which is how bank names actually vary. WRatio is deliberately
        # NOT used: it floors unrelated short strings at ~60 and would
        # make every query look like a weak match.
        scored: list[BankMatch] = []
        for bank in banks:
            score = fuzz.token_set_ratio(normalized, _normalize(bank.name))
            if bank.slug:
                score = max(score, fuzz.token_set_ratio(normalized, _normalize(bank.slug)))
            if score >= _MIN_SCORE:
                scored.append(BankMatch(bank.code, bank.name, int(score), False))

        scored.sort(key=lambda m: (-m.score, m.name))
        return scored[:_MAX_MATCHES]

    # ── Snapshot ───────────────────────────────────────────────────

    @staticmethod
    def _read_snapshot() -> list[BankInfo]:
        if not SNAPSHOT_PATH.exists():
            return []
        try:
            payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("could not read bank snapshot at %s", SNAPSHOT_PATH)
            return []

        rows = payload.get("banks") if isinstance(payload, dict) else payload
        out: list[BankInfo] = []
        for row in rows or []:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if not code or not name:
                continue
            out.append(
                BankInfo(
                    code=code,
                    name=name,
                    slug=row.get("slug"),
                    type=row.get("type"),
                    country=str(row.get("country") or "nigeria").lower(),
                    active=bool(row.get("active", True)),
                )
            )
        return out

    @staticmethod
    def _write_snapshot(infos: list[BankInfo]) -> None:
        """Best-effort refresh of the committed snapshot file."""
        payload = {
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "banks": [
                {
                    "code": i.code,
                    "name": i.name,
                    "slug": i.slug,
                    "type": i.type,
                    "country": i.country,
                    "active": i.active,
                }
                for i in infos
            ],
        }
        try:
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_PATH.write_text(
                json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.warning("could not write bank snapshot at %s", SNAPSHOT_PATH)


def get_bank_directory(provider: PaymentProvider | None = None) -> BankDirectory:
    """Build a directory bound to the configured payment provider."""
    if provider is None:
        from app.services.payments import get_payment_provider

        provider = get_payment_provider()
    return BankDirectory(provider)
