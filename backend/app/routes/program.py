"""Program endpoints — for external automations that authenticate with an API key.

The research-voter reads the caller's Robinhood research (watchlists), keeps the
S&P 500 names, takes the top 20, and casts a conviction-weighted vote. Works with
either an API key (`Authorization: Bearer ck_...` / `X-API-Key`) or a normal
session, so the same endpoint backs the standalone script and the forge button.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..services import research
from .deps import require_member

router = APIRouter(prefix="/api/program", tags=["program"])


class ResearchVoteBody(BaseModel):
    symbols: list[str] | None = None   # override the live research read (optional)
    dry_run: bool = False


@router.post("/research-vote")
def research_vote(body: ResearchVoteBody, member: dict = Depends(require_member)):
    """Read research → S&P 500 top-20 → conviction-weighted distribution → vote."""
    return research.research_vote(member["id"], override=body.symbols, dry=body.dry_run)


@router.get("/research-preview")
def research_preview(member: dict = Depends(require_member)):
    """Same computation, but never casts a vote — see what the program would do."""
    return research.research_vote(member["id"], dry=True)
