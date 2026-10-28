"""Bank directory API - powers the SPA's bank picker.

Mounted at /api/v1/banks in `app.main`.

The picker exists because every transfer needs a numeric-ish bank code
("058") while users think in names ("GTBank"). Resolving locally means
no Paystack round-trip per keystroke, and returning a *list* for
ambiguous input means we never silently send money to the wrong bank.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.bank import Bank
from app.models.user import User
from app.schemas.bank import BankOption, BankResolveResponse
from app.services.auth import get_current_active_user
from app.services.banks import get_bank_directory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["banks"])


@router.get("", response_model=list[BankOption])
def search_banks(
    q: str = Query(default="", max_length=128, description="Bank name, alias or code"),
    limit: int = Query(default=20, ge=1, le=50),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> list[BankOption]:
    """Search the bank directory.

    An empty `q` returns the first `limit` active banks alphabetically,
    which is what the dropdown shows before the user types anything.
    """
    directory = get_bank_directory()
    directory.ensure_loaded(session)

    if not q.strip():
        rows = session.exec(
            select(Bank)
            .where(Bank.active.is_(True))
            .order_by(Bank.name)
            .limit(limit)
        ).all()
        return [BankOption(code=r.code, name=r.name, slug=r.slug) for r in rows]

    matches = directory.resolve_name(session, q)
    return [
        BankOption(code=m.code, name=m.name, exact=m.exact, score=m.score)
        for m in matches[:limit]
    ]


@router.get("/resolve", response_model=BankResolveResponse)
def resolve_bank(
    q: str = Query(min_length=1, max_length=128),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BankResolveResponse:
    """Resolve a typed bank name to a single code, or return candidates.

    `exact` is populated only when the answer is unambiguous; otherwise
    the client must present `matches` and store the chosen `code`.
    """
    directory = get_bank_directory()
    directory.ensure_loaded(session)

    matches = directory.resolve_name(session, q)
    exact = matches[0] if len(matches) == 1 and matches[0].exact else None

    return BankResolveResponse(
        query=q,
        exact=(
            BankOption(code=exact.code, name=exact.name, exact=True)
            if exact is not None
            else None
        ),
        matches=[
            BankOption(code=m.code, name=m.name, exact=m.exact, score=m.score)
            for m in matches
        ],
    )
