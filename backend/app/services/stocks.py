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
from datetime import datetime

from ..calendar_us import ET
from ..db import db
from ..robinhood import vault
from . import market
from .performance import _parse_historicals

SYMBOL_RE = re.compile(r"^[A-Z.]{1,10}$")

QF_TTL_S = 300        # quote + fundamentals — intraday numbers, but not tick data
HIST_TTL_S = 3600     # daily closes move once a day; an hour is generous
NEG_TTL_S = 300       # confirmed-unknown memo, so garbage symbols can't hammer RH

_RANGE_DAYS = {"1M": 31, "3M": 92, "1Y": 366}

# Each range gets a REAL interval and window. Before this, ONE 1-year daily call
# served everything and "1D" did not exist — a day's worth of daily bars is a
# single point, so the shortest honest view the old code could draw was a month.
#
# Bar counts are kept a few hundred at most: the upstream REJECTS a call whose
# (interval, window) would blow its cap. 1D over 5-minute RTH bars is ~78; 1W
# over 30-minute is ~65; the daily ranges are 22 / 65 / 252.
#
# `intraday` decides the point shape: intraday points carry `t` (epoch seconds,
# a date alone cannot place a 5-minute bar), daily points carry `d`.
_RANGES: dict[str, dict] = {
    # 5 days back, then trimmed to the LAST session by _last_session_only. A
    # 1-day window looks right and is wrong: asked on a Sunday it spans only
    # Saturday and returns an empty chart (caught exactly that way — a weekend
    # test run came back with 0 bars). Five days clears a weekend plus a public
    # holiday. ~78 RTH bars a day, so ~390 at most: well inside the bar cap.
    "1D": {"days": 5,   "interval": "5minute",  "bounds": "regular", "intraday": True},
    "1W": {"days": 7,   "interval": "30minute", "bounds": "regular", "intraday": True},
    "1M": {"days": 31,  "interval": "day",      "bounds": None,      "intraday": False},
    "3M": {"days": 92,  "interval": "day",      "bounds": None,      "intraday": False},
    "1Y": {"days": 366, "interval": "day",      "bounds": None,      "intraday": False},
}
DEFAULT_RANGE = "3M"          # what the detail payload ships with, so first
                              # paint never spends an intraday call
RANGE_TTL_S = {"1D": 60, "1W": 300}   # intraday moves; daily falls back to HIST_TTL_S

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


# The broker's daily bars carry OHLCV; the quote endpoint carries no day range
# at all, so the session's open/high/low/volume are read off the LAST bar of the
# 1-year call we already make — no extra broker round-trip.
_BAR_KEYS = {
    "open": ("open_price", "open", "o"),
    "high": ("high_price", "high", "h"),
    "low": ("low_price", "low", "l"),
    "volume": ("volume", "v"),
}
_EMPTY_DAY = {"d": None, "open": None, "high": None, "low": None, "volume": None}


def _bar_results(payload) -> list:
    """The per-symbol result rows of a historicals payload (same defensive
    unwrapping as performance._parse_historicals)."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if isinstance(data, dict):
        return data.get("results") or []
    return data if isinstance(data, list) else []


def _last_bar(payload, symbol: str) -> dict:
    """{d, open, high, low, volume} of the most recent daily bar for `symbol`.

    Nulls (never a raise) when the payload is shaped differently or the broker
    doesn't carry a field — the day-range strip just hides itself.
    """
    best: tuple[str, dict] | None = None
    for r in _bar_results(payload):
        if not isinstance(r, dict) or str(r.get("symbol") or "").upper() != symbol:
            continue
        for b in r.get("bars") or r.get("historicals") or r.get("data_points") or []:
            if not isinstance(b, dict):
                continue
            d = b.get("begins_at") or b.get("timestamp") or b.get("t")
            if not d:
                continue
            d = str(d)[:10]
            if best is None or d >= best[0]:
                best = (d, b)
    if best is None:
        return dict(_EMPTY_DAY)
    day = dict(_EMPTY_DAY, d=best[0])
    for key, spellings in _BAR_KEYS.items():
        for s in spellings:
            v = _num(best[1].get(s))
            if v is not None:
                day[key] = v
                break
    return day


def _bar_ts(raw) -> int | None:
    """RFC3339 bar stamp ("2026-07-30T13:30:00Z") → epoch seconds."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _parse_intraday(payload, symbol: str) -> list[dict]:
    """[{t: epoch seconds, c: close}] ascending, full timestamps preserved.

    performance._parse_historicals cannot be reused here: it keys bars by
    str(date)[:10], which collapses every 5-minute bar of a day onto ONE entry —
    fine for daily closes, fatal for an intraday chart.
    """
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    results = (data.get("results") if isinstance(data, dict) else data) or []
    out: list[dict] = []
    for r in results:
        if not isinstance(r, dict) or str(r.get("symbol") or "").upper() != symbol:
            continue
        for b in r.get("bars") or r.get("historicals") or r.get("data_points") or []:
            if not isinstance(b, dict):
                continue
            close = _num(b.get("close_price"))
            if close is None:
                close = _num(b.get("close"))
            if close is None:
                close = _num(b.get("c"))
            ts = _bar_ts(b.get("begins_at") or b.get("timestamp") or b.get("t"))
            if close is None or ts is None:
                continue
            out.append({"t": ts, "c": close})
    out.sort(key=lambda p: p["t"])
    return out


def _et_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


def _last_session_only(pts: list[dict]) -> list[dict]:
    """Trim an intraday series to its most recent SESSION.

    1D asks for a few days of bars on purpose — a 1-day window returns nothing
    on a Monday morning, a weekend, or a holiday. Keeping only the last ET date
    present makes "1D" mean the last session that actually traded, whenever it
    was, instead of an empty chart.
    """
    if not pts:
        return []
    last = _et_date(pts[-1]["t"])
    return [p for p in pts if _et_date(p["t"]) == last]


def _day_from_intraday(pts: list[dict]) -> dict:
    """Session OHLC from real intraday bars, tagged with WHICH session it is.

    The old day-range strip read the last DAILY bar and displayed it as "Day
    range" — pre-open, on a weekend or a holiday that is the PREVIOUS session
    being passed off as today. is_today lets the UI say which it is.
    """
    if not pts:
        return dict(_EMPTY_DAY)
    cs = [p["c"] for p in pts]
    session = _et_date(pts[-1]["t"])
    return {
        "d": session,
        "session_date": session,
        "is_today": session == _et_date(time.time()),
        "open": cs[0],
        "high": max(cs),
        "low": min(cs),
        "volume": None,
    }


def _fetch_range(client, symbol: str, rng: str) -> list[dict]:
    """One range's real bars, at that range's own interval."""
    spec = _RANGES[rng]
    start = time.strftime("%Y-%m-%dT00:00:00Z",
                          time.gmtime(time.time() - spec["days"] * 86400))
    try:
        payload = client.get_equity_historicals(
            [symbol], start, interval=spec["interval"], bounds=spec["bounds"])
    except Exception:  # noqa: BLE001
        return []
    if spec["intraday"]:
        pts = _parse_intraday(payload, symbol)
        return _last_session_only(pts) if rng == "1D" else pts
    series = _parse_historicals(payload).get(symbol) or {}
    return [{"d": d, "c": series[d]} for d in sorted(series)]


def history(symbol: str, rng: str) -> dict:
    """A single range on demand, cached per (symbol, range).

    Intraday ranges carry a short TTL because they move; daily ranges reuse the
    hourly one. Flipping tabs therefore costs the broker nothing after the first
    look at each range.
    """
    symbol = (symbol or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise UnknownSymbol("no such symbol")
    if rng not in _RANGES:
        raise UnknownSymbol("no such range")
    now = time.time()
    ent = _cache.setdefault(symbol, {})
    key, at_key = f"r:{rng}", f"r_at:{rng}"
    ttl = RANGE_TTL_S.get(rng, HIST_TTL_S)
    if ent.get(at_key, 0) > now - ttl:
        pts = ent.get(key) or []
    else:
        client = _client()
        pts = _fetch_range(client, symbol, rng) if client is not None else []
        ent[key] = pts
        # An empty series retries on the short cadence — a transient broker miss
        # must not blank a chart for the full hour.
        ent[at_key] = now if pts else now - (ttl - 60)
    out = {"symbol": symbol, "range": rng, "points": pts,
           "intraday": bool(_RANGES[rng]["intraday"]), "as_of": int(now)}
    if rng == "1D":
        out["day"] = _day_from_intraday(pts)
    return out


def _fetch_history(client, symbol: str) -> tuple[list[dict], dict]:
    """([{d: ISO date, c: close}] ascending, last bar's {d,open,high,low,volume})
    — ONE 1-year daily-bars call; the shorter ranges are sliced from it and the
    day range is read off its last bar, never fetched separately."""
    start = time.strftime("%Y-%m-%dT00:00:00Z",
                          time.gmtime(time.time() - _RANGE_DAYS["1Y"] * 86400))
    try:
        payload = client.get_equity_historicals([symbol], start, interval="day")
    except Exception:  # noqa: BLE001
        return [], dict(_EMPTY_DAY)
    series = _parse_historicals(payload).get(symbol) or {}
    return ([{"d": d, "c": series[d]} for d in sorted(series)],
            _last_bar(payload, symbol))


def _slice_ranges(hist: list[dict]) -> dict:
    now = time.time()
    out: dict = {}
    for label, days in _RANGE_DAYS.items():
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(now - days * 86400))
        out[label] = [p for p in hist if p["d"] >= cutoff]
    return out


# ─── the one detail payload ──────────────────────────────────────────────────

def detail(symbol: str) -> dict:
    """The stock-detail view: quote + day range + 1M/3M/1Y closes + fundamentals.

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
    day = ent.get("day") if hist_fresh else None
    if hist is None and client is not None:
        hist, day = _fetch_history(client, symbol)
        # an EMPTY series retries on the quote cadence, not a full hour — a
        # transient broker miss must not blank the charts for 60 minutes
        ent["hist"], ent["day"] = hist, day
        ent["hist_at"] = now if hist else now - (HIST_TTL_S - QF_TTL_S)

    return {
        "symbol": symbol,
        "name": name,
        "quote": (qf or {}).get("quote") or {"price": None, "prev_close": None,
                                             "change_pct": None},
        "range": _slice_ranges(hist or []),
        "day": day or dict(_EMPTY_DAY),
        "fundamentals": (qf or {}).get("fundamentals")
                        or {k: None for k in (*_FUND_NUM_KEYS, *_FUND_STR_KEYS)},
        "in_universe": in_universe,
        "as_of": int(ent.get("qf_at") or now),
    }
