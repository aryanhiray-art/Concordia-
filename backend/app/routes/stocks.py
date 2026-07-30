"""Stock data endpoints: universe search + the per-symbol detail payload."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..services import stocks as svc
from ..services import voting as voting_svc
from .deps import require_member

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# Registered BEFORE /{symbol} so the literal path wins the match.
@router.get("/search")
def search_stocks(q: str = "", limit: int = 20, member: dict = Depends(require_member)):
    """Search the votable universe by symbol or company name — the same matcher
    as GET /api/universe (services/voting.universe_search), served at the path
    the stock search page calls. Each hit is {symbol, name}."""
    limit = max(1, min(int(limit), 50))
    return {"results": voting_svc.universe_search(q, limit)}


@router.get("/{symbol}")
def stock_detail(symbol: str, member: dict = Depends(require_member)):
    """One payload for the stock-detail view: quote, the latest session's
    day range (open/high/low/volume), 1M/3M/1Y closes, normalized fundamentals,
    universe membership. 404 on garbage."""
    try:
        return svc.detail(symbol)
    except svc.UnknownSymbol:
        raise HTTPException(status_code=404, detail="no such symbol")
