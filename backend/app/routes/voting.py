"""Voting endpoints: universe, today's ballot, cast picks, results."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import db
from ..services import voting as svc
from .deps import require_member

router = APIRouter(prefix="/api", tags=["voting"])


class AllocItem(BaseModel):
    symbol: str
    weight: float = 0   # % of the member's voting power on this pick
    thesis: str | None = None   # optional rationale, sanitized+capped server-side


class VoteBody(BaseModel):
    allocations: list[AllocItem] | None = None   # preferred (Concordia model)
    symbols: list[str] | None = None             # back-compat: equal-weighted


@router.get("/universe")
def get_universe(q: str = "", limit: int = 40):
    """Search the S&P 500 universe (symbol or company name)."""
    return {"symbols": svc.universe_search(q, limit)}


@router.get("/vote/today")
def vote_today(member: dict = Depends(require_member)):
    session = svc.ensure_active_session()
    return svc.session_public(session, member["id"])


@router.post("/vote/today")
def cast_vote(body: VoteBody, member: dict = Depends(require_member)):
    s = member.get("settings") or {}
    if int(s.get("opted_in", 0)) != 1:
        raise HTTPException(status_code=403, detail="participation is off — turn it on to vote")
    session = svc.ensure_active_session()   # always resolves to the open ballot
    if session["status"] != "open":
        raise HTTPException(status_code=409, detail="voting is closed")
    try:
        if body.allocations is not None:
            svc.set_member_allocations(session["id"], member["id"],
                                       [{"symbol": a.symbol, "weight": a.weight, "thesis": a.thesis}
                                        for a in body.allocations])
        else:
            svc.set_member_picks(session["id"], member["id"], body.symbols or [])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return svc.session_public(svc.get_session(session["id"]), member["id"])


@router.get("/me/votes")
def my_votes(limit: int = 30, member: dict = Depends(require_member)):
    """The caller's own ballots, newest first — the PERSONAL half of History.

    Only sessions this member voted in. Open sessions show the caller's ballot
    (it's theirs) with basket/hits empty until close; the club-shared record
    lives at GET /api/club/history.
    """
    return {"sessions": svc.member_vote_history(member["id"], limit)}


@router.get("/vote/results")
def vote_results(member: dict = Depends(require_member)):
    """Latest session's consensus picks + this member's execution rows (owner sees all)."""
    session = svc.active_session()
    if not session:
        return {"session": None}
    is_owner = member.get("role") == "owner"
    with db() as conn:
        if is_owner:
            rows = conn.execute(
                "SELECT e.*, m.display_name FROM executions e JOIN members m ON m.id=e.member_id "
                "WHERE e.session_id=? ORDER BY e.member_id, e.symbol", (session["id"],)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM executions WHERE session_id=? AND member_id=? ORDER BY symbol",
                (session["id"], member["id"])
            ).fetchall()
    # The most recent DECIDED basket (closed/executed/lapsed), so the UI always
    # has a basket to show even when the active session's ballot is still empty
    # (e.g. early morning before the bots vote, after a full liquidation).
    with db() as conn:
        prev = conn.execute(
            "SELECT * FROM vote_sessions WHERE status IN ('closed','executed','lapsed') "
            "AND picks_json IS NOT NULL ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    last_decided = ({"trade_date": prev["trade_date"], "picks": svc.session_picks(dict(prev))}
                    if prev else None)
    return {
        "session": svc.session_public(session, member["id"]),
        "executions": [dict(r) for r in rows],
        "last_decided": last_decided,
    }
