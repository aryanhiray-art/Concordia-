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


# Also before /{symbol}: "/AAPL/history" must not be read as a symbol.
@router.get("/{symbol}/history")
def stock_history(symbol: str, range: str = svc.DEFAULT_RANGE,
                  member: dict = Depends(require_member)):
    """One range's real bars, on demand — the detail payload ships only the
    default range so a first visit never spends an intraday call.

    1D/1W are genuine intraday series (5-minute / 30-minute bars) and return
    points as {t: epoch_seconds, c}; 1M/3M/1Y are daily and return {d, c}.
    1D also carries `day` (session OHLC + session_date + is_today) so the UI can
    say WHICH session it is showing rather than implying it is today's.
    """
    try:
        return svc.history(symbol, (range or "").strip().upper())
    except svc.UnknownSymbol as e:
        raise HTTPException(status_code=404, detail=str(e) or "no such symbol")


@router.get("/{symbol}")
def stock_detail(symbol: str, member: dict = Depends(require_member)):
    """One payload for the stock-detail view: quote, the latest session's
    day range (open/high/low/volume), 1M/3M/1Y closes, normalized fundamentals,
    universe membership. 404 on garbage."""
    try:
        return svc.detail(symbol)
    except svc.UnknownSymbol:
        raise HTTPException(status_code=404, detail="no such symbol")
