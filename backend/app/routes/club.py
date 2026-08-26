"""Club-level endpoints — the same view for every member.

Performance = the club's decided trades (equal-weight decisions index) vs an
S&P 500 buy-and-hold, computed once and shared (not per-account).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..services import clubmirror, performance
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
    # The DECISIONS index (services/performance): what the club's chosen basket
    # returned equal-weight vs SPY buy-and-hold. It is PAPER and the UI says so,
    # but it is the only series here that has been verified end to end — an
    # independent recomputation over all 24 locked baskets, with every price
    # spot-checked against Robinhood, reproduces it exactly (+6.85% vs +3.04%
    # for 06-17..08-24).
    #
    # The real-money record (services/clubnav) is still computed and stored
    # nightly — it is simply not honest enough to publish yet:
    #   * Through 2026-08-24 it measured members' WHOLE brokerage accounts, so
    #     idle cash and personal positions counted as club performance. A member
    #     who joined with $1,000 in uninvested cash was 88% of the weight and
    #     returned 0.00% for five days, pinning the line flat.
    #   * daily_values() gates each member on their CURRENT allocation and
    #     applies it to ALL history, so one settings change on 2026-08-25
    #     silently rewrote the record from +2.05% to +9.76%.
    # club_value — the actual club-managed slice — only began recording
    # 2026-08-24, so the honest real-money series needs history it does not have
    # yet. Revisit once that column has a real run behind it.
    return performance.club_performance(
        force=bool(force and member.get("role") == "owner"))


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
