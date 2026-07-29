"""Club-level endpoints — the same view for every member.

Performance = the club's decided trades (equal-weight decisions index) vs an
S&P 500 buy-and-hold, computed once and shared (not per-account).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..services import performance
from ..services import voting as voting_svc
from .deps import require_member

router = APIRouter(prefix="/api/club", tags=["club"])


@router.get("/performance")
def club_performance(force: bool = False, member: dict = Depends(require_member)):
    """Decided-trades index vs SPY buy-and-hold. `force` recompute is honored
    for the owner only — a member hammering it must not bust the shared cache."""
    return performance.club_performance(force=force and member.get("role") == "owner")


@router.get("/history")
def club_history(limit: int = 30, member: dict = Depends(require_member)):
    """The club's decided sessions, newest first — the SHARED fund record.

    Identical for every member (nothing member-identifying); a member's own
    ballots live at GET /api/me/votes instead.
    """
    return {"sessions": voting_svc.club_history(limit)}
