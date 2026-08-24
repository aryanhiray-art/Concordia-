"""Club-level endpoints — the same view for every member.

Performance = the club's decided trades (equal-weight decisions index) vs an
S&P 500 buy-and-hold, computed once and shared (not per-account).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..services import clubmirror, clubnav, performance
from ..services import voting as voting_svc
from .deps import require_member

router = APIRouter(prefix="/api/club", tags=["club"])


@router.get("/performance")
def club_performance(force: bool = False, member: dict = Depends(require_member)):
    """Decided-trades index vs SPY buy-and-hold. `force` recompute is honored
    for the owner only — a member hammering it must not bust the shared cache.

    On the DEMO instance this serves the REAL club's series from the mirror file
    (services/clubmirror): demo accounts trade fake money, but the club's record
    is the actual one. Falls through to local data when no mirror is present.
    """
    mirrored = clubmirror.performance()
    if mirrored is not None:
        return mirrored
    # The REAL-money record (services/clubnav), read from the kept ledger.
    # The old paper decisions-index is retained in services/performance for
    # comparison but is no longer what the club claims as its track record.
    if force and member.get("role") == "owner":
        clubnav.refresh()
    served = clubnav.series()
    if served["points"]:
        return served
    # Ledger not built yet (fresh deploy) — build it once, then serve.
    clubnav.refresh()
    return clubnav.series()


@router.get("/history")
def club_history(limit: int = 30, member: dict = Depends(require_member)):
    """The club's decided sessions, newest first — the SHARED fund record.

    Identical for every member (nothing member-identifying) and operations-free
    (no voter counts, no order tallies — that detail is admin-only at
    GET /api/admin/cycles); a member's own ballots live at GET /api/me/votes
    instead.

    On the DEMO instance this serves the REAL club's cycles from the mirror file
    — the fund's record is shared and non-identifying, so the demo shows the
    actual one instead of fixtures. Member-level views stay simulated.
    """
    mirrored = clubmirror.history(limit)
    if mirrored is not None:
        return {"sessions": mirrored, "mirrored_at": clubmirror.mirrored_at()}
    return {"sessions": voting_svc.club_history(limit)}
