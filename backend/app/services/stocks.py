"""Per-symbol stock data for the search + detail pages.

Reads borrow ANY connected member's Robinhood client (the services/market.py
pattern) — read-only, never trades. Every symbol's data is cached in-process
(quote+fundamentals for QF_TTL_S, daily closes for HIST_TTL_S) so members
clicking around the detail UI can never hammer Robinhood: after the first view
a symbol costs the broker nothing until the TTL lapses. The 1M/3M ranges are
sliced server-side out of ONE 1-year historicals call, never fetched separately.

DEMO_MODE flows through unchanged: vault.ensure_client hands back a DemoClient
whose quotes/fundamentals/historicals are deterministic fakes, so the whole
detail page renders end-to-end with zero external calls.
"""
from __future__ import annotations

import re
import time

from ..db import db
from ..robinhood import vault
from . import market
from .performance import _parse_historicals

SYMBOL_RE = re.compile(r"^[A-Z.]{1,10}$")

QF_TTL_S = 300        # quote + fundamentals — intraday numbers, but not tick data
HIST_TTL_S = 3600     # daily closes move once a day; an hour is generous
NEG_TTL_S = 300       # confirmed-unknown memo, so garbage symbols can't hammer RH

_RANGE_DAYS = {"1M": 31, "3M": 92, "1Y": 366}

# symbol -> {"qf": {...}, "qf_at": epoch, "hist": [{d, c}], "hist_at": epoch,
#            "missing_at": epoch}
_cache: dict[str, dict] = {}


class UnknownSymbol(LookupError):
    """Malformed, or confirmed absent from both the universe and the broker."""


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _client():
    """A borrowed read-only client from any connected member (market.py pattern).
    None when nobody's connected or the client can't be built — never raises."""
    mid = market._any_connected_member()
    if mid is None:
        return None
    try:
        return vault.ensure_client(mid)
    except Exception:  # noqa: BLE001
        return None


def _universe_name(symbol: str) -> tuple[bool, str]:
    """(in_universe, display name — the universe's, else the symbol echoed)."""
    with db() as conn:
        row = conn.execute(
            "SELECT name FROM universe WHERE symbol=? AND active=1", (symbol,)
        ).fetchone()
    if row is None:
        return False, symbol
    return True, row["name"] or symbol


# ─── broker reads (each defensive, each returns nulls rather than raising) ────

def _fetch_quote(client, symbol: str) -> dict:
    quote = {"price": None, "prev_close": None, "change_pct": None}
    try:
        res = (client.get_quotes([symbol]).get("data") or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return quote
    for e in res:
        q = e.get("quote") or e
        if (q.get("symbol") or "").upper() != symbol:
            continue
        quote["price"] = _num(q.get("last_trade_price")) or _num(q.get("price"))
        quote["prev_close"] = (_num(q.get("previous_close"))
                               or _num(q.get("adjusted_previous_close")))
        if quote["price"] and quote["prev_close"]:
            quote["change_pct"] = round(
                (quote["price"] - quote["prev_close"]) / quote["prev_close"] * 100, 2)
    return quote


# fixed payload contract -> the upstream key spellings we accept, in order
_FUND_NUM_KEYS = {
    "market_cap": ("market_cap",),
    "pe_ratio": ("pe_ratio",),
    "dividend_yield": ("dividend_yield",),
    "high_52w": ("high_52_weeks", "high_52w", "week_52_high"),
    "low_52w": ("low_52_weeks", "low_52w", "week_52_low"),
    "volume": ("volume",),
    "avg_volume": ("average_volume", "average_volume_2_weeks", "avg_volume"),
}
_FUND_STR_KEYS = {"sector": ("sector",), "description": ("description",)}


def _fetch_fundamentals(client, symbol: str) -> dict:
    """get_equity_fundamentals normalized to the fixed contract, nulls where absent."""
    out: dict = {k: None for k in (*_FUND_NUM_KEYS, *_FUND_STR_KEYS)}
    try:
        payload = client.get_equity_fundamentals([symbol])
    except Exception:  # noqa: BLE001
        return out
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    results = (data.get("results") or []) if isinstance(data, dict) else \
        (data if isinstance(data, list) else [])
    for e in results:
        if not isinstance(e, dict):
            continue
        f = e.get("fundamentals") if isinstance(e.get("fundamentals"), dict) else e
        sym = (e.get("symbol") or f.get("symbol") or "").upper()
        if sym and sym != symbol:
            continue
        for key, spellings in _FUND_NUM_KEYS.items():
            for s in spellings:
                if f.get(s) is not None and _num(f.get(s)) is not None:
                    out[key] = _num(f.get(s))
                    break
        for key, spellings in _FUND_STR_KEYS.items():
            for s in spellings:
                if f.get(s):
                    out[key] = str(f.get(s))
                    break
    return out


def _fetch_history(client, symbol: str) -> list[dict]:
    """[{d: ISO date, c: close}] ascending — ONE 1-year daily-bars call; the
    shorter ranges are sliced from it, never fetched separately."""
    start = time.strftime("%Y-%m-%dT00:00:00Z",
                          time.gmtime(time.time() - _RANGE_DAYS["1Y"] * 86400))
    try:
        payload = client.get_equity_historicals([symbol], start, interval="day")
    except Exception:  # noqa: BLE001
        return []
    series = _parse_historicals(payload).get(symbol) or {}
    return [{"d": d, "c": series[d]} for d in sorted(series)]


def _slice_ranges(hist: list[dict]) -> dict:
    now = time.time()
    out: dict = {}
    for label, days in _RANGE_DAYS.items():
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(now - days * 86400))
        out[label] = [p for p in hist if p["d"] >= cutoff]
    return out


# ─── the one detail payload ──────────────────────────────────────────────────

def detail(symbol: str) -> dict:
    """The stock-detail view: quote + 1M/3M/1Y closes + normalized fundamentals.

    Raises UnknownSymbol for a malformed symbol, or one that is neither in the
    universe nor priceable at the broker (the caller maps it to a clean 404).
    A universe symbol always serves — with nulls when the broker is unreachable.
    """
    symbol = (symbol or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise UnknownSymbol("no such symbol")
    in_universe, name = _universe_name(symbol)
    now = time.time()
    ent = _cache.setdefault(symbol, {})
    if not in_universe and ent.get("missing_at", 0) > now - NEG_TTL_S:
        raise UnknownSymbol("no such symbol")

    qf_fresh = ent.get("qf_at", 0) > now - QF_TTL_S
    hist_fresh = ent.get("hist_at", 0) > now - HIST_TTL_S
    client = _client() if not (qf_fresh and hist_fresh) else None

    qf = ent.get("qf") if qf_fresh else None
    if qf is None and client is not None:
        qf = {"quote": _fetch_quote(client, symbol),
              "fundamentals": _fetch_fundamentals(client, symbol)}
        ent["qf"], ent["qf_at"] = qf, now

    if not in_universe and not ((qf or {}).get("quote") or {}).get("price"):
        # Outside the votable universe a symbol is "real" only if the broker
        # prices it; otherwise it's garbage — remember that (only when a broker
        # was actually consulted) and never spend a historicals call on it.
        if client is not None:
            ent["missing_at"] = now
        raise UnknownSymbol("no such symbol")

    hist = ent.get("hist") if hist_fresh else None
    if hist is None and client is not None:
        hist = _fetch_history(client, symbol)
        # an EMPTY series retries on the quote cadence, not a full hour — a
        # transient broker miss must not blank the charts for 60 minutes
        ent["hist"] = hist
        ent["hist_at"] = now if hist else now - (HIST_TTL_S - QF_TTL_S)

    return {
        "symbol": symbol,
        "name": name,
        "quote": (qf or {}).get("quote") or {"price": None, "prev_close": None,
                                             "change_pct": None},
        "range": _slice_ranges(hist or []),
        "fundamentals": (qf or {}).get("fundamentals")
                        or {k: None for k in (*_FUND_NUM_KEYS, *_FUND_STR_KEYS)},
        "in_universe": in_universe,
        "as_of": int(ent.get("qf_at") or now),
    }
