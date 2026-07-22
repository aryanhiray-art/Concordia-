"""Club performance: trades decided vs an S&P 500 buy-and-hold.

The club's track record is the *decisions*, not any one member's account (sizing
differs per member; accounts hold personal positions). Model: an equal-weight
"decisions index" — each decided session (one that actually placed orders) sets
the basket; every day the index compounds the mean close-to-close return of the
basket that was in effect the *previous* trading day. The benchmark is SPY
bought at the first decided session and held. Both series are indexed to 100 at
the first trading day, so the chart reads as growth-of-$100. Club-level: the
same series for every member.

READ-ONLY guarantee: this module never imports the executor and never calls a
review/place method — it borrows a connected member's client for historical
closes only (same borrow pattern as services/market.py).
"""
from __future__ import annotations

import json
import time

from ..db import audit, db, kv_get, kv_set
from ..robinhood import vault
from . import market

_BENCHMARK = "SPY"
_CACHE_KEY = "club_perf_v1"
_CACHE_TTL_S = 6 * 3600           # recompute at most every 6h (or when a new session lands)
_RETRY_TTL_S = 15 * 60            # backoff between attempts after a failed/partial fetch

_EMPTY = {"start_date": None, "computed_at": 0, "n_sessions": 0,
          "points": [], "club_pct": None, "spy_pct": None}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── layer 1: which sessions actually moved money ────────────────────────────

def decided_cycles() -> list[dict]:
    """Sessions with at least one PLACED execution, oldest first.

    [{"trade_date": "YYYY-MM-DD", "symbols": [...]}] — symbols from the locked
    picks_json. Sessions with no picks are skipped (nothing to index).
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT vs.trade_date, vs.picks_json FROM vote_sessions vs "
            "JOIN (SELECT DISTINCT session_id FROM executions WHERE status='placed') e "
            "ON e.session_id = vs.id ORDER BY vs.trade_date ASC"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            picks = json.loads(r["picks_json"] or "[]")
        except Exception:  # noqa: BLE001
            picks = []
        symbols = [str(p["symbol"]).upper() for p in picks
                   if isinstance(p, dict) and p.get("symbol")]
        if not symbols:
            continue
        out.append({"trade_date": r["trade_date"], "symbols": symbols})
    return out


# ─── layer 2: pure series math (no I/O) ──────────────────────────────────────

def build_series(cycles: list[dict], bars: dict, benchmark: str = _BENCHMARK) -> dict:
    """Compound the decisions index vs the benchmark from daily closes.

    `bars` = {symbol: {"YYYY-MM-DD": close}}. Timeline = the benchmark's dates
    from the first decided trade_date on. Each day compounds the mean return of
    the basket decided by the LATEST cycle with trade_date <= the previous
    timeline date (a decision takes effect the day after it's made). A symbol
    missing either close drops out of that day's mean; a day with no priced
    basket symbol is flat. Caller fills computed_at.
    """
    if not cycles:
        return dict(_EMPTY)
    spy = bars.get(benchmark) or {}
    first_trade = cycles[0]["trade_date"]
    timeline = sorted(d for d in spy if d >= first_trade)
    if not timeline:
        return dict(_EMPTY)
    spy_first = spy[timeline[0]]
    if not spy_first or spy_first <= 0:
        return dict(_EMPTY)
    club = 100.0
    points = [{"d": timeline[0], "club": 100.0, "spy": 100.0}]
    for prev, d in zip(timeline, timeline[1:]):
        basket: list[str] = []
        for c in cycles:
            if c["trade_date"] <= prev:
                basket = c["symbols"]
            else:
                break
        rets = []
        for s in basket:
            sb = bars.get(s) or {}
            p0, p1 = sb.get(prev), sb.get(d)
            if p0 is not None and p1 is not None and p0 > 0:
                rets.append(p1 / p0 - 1.0)
        day_return = sum(rets) / len(rets) if rets else 0.0
        club *= 1.0 + day_return
        points.append({"d": d, "club": round(club, 4),
                       "spy": round(100.0 * spy[d] / spy_first, 4)})
    return {
        "start_date": timeline[0],
        "computed_at": 0,
        "n_sessions": len(cycles),
        "points": points,
        "club_pct": round(points[-1]["club"] - 100.0, 2),
        "spy_pct": round(points[-1]["spy"] - 100.0, 2),
    }


# ─── layer 3: fetch + cache orchestration ────────────────────────────────────

def _parse_historicals(payload) -> dict[str, dict[str, float]]:
    """{symbol: {date: close}} from a get_equity_historicals payload.

    Defensive: the upstream wraps tool output as {data:{results:[...]}} or
    {results:[...]}; each result carries its bars under "bars"/"historicals"/
    "data_points" with close as "close_price"/"close"/"c" and the date as
    "begins_at"/"timestamp"/"t". A bar with no usable close (incl. an
    interpolated placeholder) is skipped.
    """
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if isinstance(data, dict):
        results = data.get("results") or []
    elif isinstance(data, list):
        results = data
    else:
        results = []
    out: dict[str, dict[str, float]] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or "").upper()
        if not sym:
            continue
        series: dict[str, float] = {}
        for b in r.get("bars") or r.get("historicals") or r.get("data_points") or []:
            if not isinstance(b, dict):
                continue
            close = _num(b.get("close_price"))
            if close is None:
                close = _num(b.get("close"))
            if close is None:
                close = _num(b.get("c"))
            if close is None:
                continue
            date = b.get("begins_at") or b.get("timestamp") or b.get("t")
            if not date:
                continue
            series[str(date)[:10]] = close
        if series:
            out[sym] = series
    return out


def _serve(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in ("ts", "ok")}


def _store(payload: dict, ok: bool) -> None:
    kv_set(_CACHE_KEY, json.dumps({**payload, "ts": int(time.time()), "ok": ok}))


def _degraded(cached: dict | None) -> dict:
    """A recompute failed or came back PARTIAL. A partial fetch silently flattens
    the missing symbols' days — a wrong-but-plausible chart — so it must never be
    served or cached. Serve the last GOOD series (or the honest empty shape when
    there's none), re-stamped ok=False so a broken feed backs off for
    _RETRY_TTL_S instead of being re-fetched on every page load."""
    payload = _serve(cached) if cached and cached.get("points") else dict(_EMPTY)
    _store(payload, ok=False)
    return payload


def club_performance(force: bool = False) -> dict:
    """The cached club-vs-SPY series, recomputed at most every 6h.

    A new decided session busts the cache immediately (n_sessions mismatch).
    Closes come from a borrowed connected member's client, read-only; a recompute
    is trusted ONLY when every historicals batch landed — anything less serves the
    last good series (see _degraded) rather than a silently-degraded one.
    """
    cycles = decided_cycles()
    cached: dict | None = None
    raw = kv_get(_CACHE_KEY)
    if raw:
        try:
            cached = json.loads(raw)
        except Exception:  # noqa: BLE001
            cached = None
    if cached is not None and not force:
        age = time.time() - cached.get("ts", 0)
        if cached.get("ok", True):
            if age < _CACHE_TTL_S and cached.get("n_sessions") == len(cycles):
                return _serve(cached)
        elif age < _RETRY_TTL_S:
            return _serve(cached)     # last attempt failed — back off before hitting RH again
    if not cycles:
        result = build_series([], {})
        result["computed_at"] = int(time.time())
        _store(result, ok=True)
        return result
    mid = market._any_connected_member()
    if mid is None:
        return _degraded(cached)
    try:
        client = vault.ensure_client(mid)
    except Exception:  # noqa: BLE001
        return _degraded(cached)
    syms = sorted({s for c in cycles for s in c["symbols"]} | {_BENCHMARK})
    start_time = f"{cycles[0]['trade_date']}T00:00:00Z"
    bars: dict[str, dict[str, float]] = {}
    fetch_ok = True
    for i in range(0, len(syms), 10):     # MCP tool caps at 10 symbols per call
        batch = syms[i:i + 10]
        try:
            payload = client.get_equity_historicals(batch, start_time, interval="day")
        except Exception as e:  # noqa: BLE001 — a failed batch fails the recompute, never raises
            audit("system", "perf_fetch_error", ",".join(batch), {"error": str(e)[:200]})
            fetch_ok = False
            continue
        for sym, series in _parse_historicals(payload).items():
            bars.setdefault(sym, {}).update(series)
    result = build_series(cycles, bars)
    # An empty build with cycles present means the benchmark bars are missing —
    # that's a fetch failure, not "no trades yet".
    if not fetch_ok or not result["points"]:
        return _degraded(cached)
    result["computed_at"] = int(time.time())
    _store(result, ok=True)
    return result
