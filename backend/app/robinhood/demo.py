"""DEMO_MODE stand-in for the Robinhood client.

When settings.demo_mode is on, vault.ensure_client returns a DemoClient instead
of ever touching Robinhood — every read returns plausible canned data and every
review passes, so the ENTIRE app (connect flow, balances, holdings, quotes,
history) works end-to-end with zero external calls and zero real tokens.
Deterministic: prices derive from the symbol name, so refreshes are stable.

Never active in production: demo_mode defaults off and config.is_live is forced
False while it is on, so a demo box can never place a real order.
"""
from __future__ import annotations

import datetime
import hashlib
import time
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# The upstream's intraday interval vocabulary -> bar width in seconds. Anything
# not listed here (day/week/month/…) takes the daily generator below. NOTE the
# 1-minute bar is spelled "minute", not "1minute" — same as the real tool.
_INTRADAY_STEP_S = {
    "15second": 15, "30second": 30, "minute": 60, "5minute": 300,
    "10minute": 600, "30minute": 1800, "hour": 3600,
}

_RTH_OPEN = (9, 30)     # ET
_RTH_CLOSE = (16, 0)    # ET


def _base_price(symbol: str) -> float:
    """The symbol's anchor price — deterministic, no time component."""
    return round(int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 50000 / 100 + 20, 2)


def _price(symbol: str) -> float:
    """Stable pseudo-price per symbol: $20–$520, varies gently by the hour."""
    wiggle = (int(time.time() // 3600) + len(symbol)) % 7 - 3
    return round(_base_price(symbol) * (1 + wiggle / 200), 2)


def _seed(symbol: str) -> int:
    """Deterministic per-symbol integer for fundamentals fakes (no time wiggle)."""
    return int(hashlib.sha256(symbol.encode()).hexdigest()[8:16], 16)


_SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials",
            "Consumer Discretionary", "Energy", "Utilities", "Communication Services"]


def _wig(*parts) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _parse_ts(v, default: float) -> int:
    """RFC3339 (or bare YYYY-MM-DD, or an epoch) -> epoch seconds; `default` on
    anything unparseable, so a malformed window degrades instead of raising."""
    if v is None:
        return int(default)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(s[:10])
        except ValueError:
            return int(default)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _rfc3339(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _session_bounds(d: datetime.date) -> tuple[int, int]:
    """(epoch open, epoch close) of d's regular ET session — DST-correct."""
    op = datetime.datetime(d.year, d.month, d.day, *_RTH_OPEN, tzinfo=_ET)
    cl = datetime.datetime(d.year, d.month, d.day, *_RTH_CLOSE, tzinfo=_ET)
    return int(op.timestamp()), int(cl.timestamp())


def _intraday_bars(sym: str, start_ts: int, end_ts: int, step_s: int) -> list[dict]:
    """Regular-hours bars of width `step_s` covering [start_ts, end_ts].

    A pure function of (symbol, bar timestamp): a slow per-session drift plus a
    small per-bar wiggle around the symbol's pseudo-price, so the demo 1D/1W
    charts show a believable intraday path that is stable across refreshes.
    Bars stop at `end_ts` — the demo never invents the future — and the final
    bar of TODAY's session is pinned to the live demo quote so the chart, the
    day range and the quote can never disagree.
    """
    import math

    p = _price(sym)
    today = datetime.datetime.now(_ET).date()
    first = datetime.datetime.fromtimestamp(start_ts, _ET).date()
    last = datetime.datetime.fromtimestamp(end_ts, _ET).date()
    bars: list[dict] = []
    d = first
    while d <= last:
        if d.weekday() >= 5:                       # weekends never trade
            d += datetime.timedelta(days=1)
            continue
        op, cl = _session_bounds(d)
        dayw = _wig(sym, d, "day")
        # older sessions sit slightly below today's level → a week trends
        base = p * (1 - 0.004 * max((today - d).days, 0))
        amp = (dayw % 220) / 10000                 # 0–2.2% intraday swing
        waves = 1 + dayw % 3
        prev_close = None
        day_start = len(bars)
        ts = op
        while ts < cl:
            if start_ts <= ts <= end_ts:
                frac = (ts - op) / max(cl - op, 1)
                w = _wig(sym, ts)
                close = base * (1 + amp * math.sin(frac * math.pi * waves)) \
                             * (1 + (w % 400 - 200) / 20000)      # ±1% noise
                open_ = prev_close if prev_close is not None else close * (
                    1 + ((w >> 9) % 40 - 20) / 20000)
                lo = min(open_, close) * (1 - ((w >> 3) % 30 + 2) / 20000)
                hi = max(open_, close) * (1 + ((w >> 7) % 30 + 2) / 20000)
                bars.append({
                    "begins_at": _rfc3339(ts),
                    "open_price": round(open_, 2), "close_price": round(close, 2),
                    "high_price": round(hi, 2), "low_price": round(lo, 2),
                    "volume": 5_000 + (w % 900) * max(step_s // 60, 1) * 40,
                })
                prev_close = close
            ts += step_s
        if d == today and len(bars) > day_start:
            # the live demo quote IS _price(sym) and drifts on the hour; today's
            # last bar must close there (and contain it) or the demo would show
            # a chart that ends somewhere the quote never was
            b = bars[-1]
            b["close_price"] = round(p, 2)
            b["high_price"] = round(max(b["high_price"], p * 1.0005), 2)
            b["low_price"] = round(min(b["low_price"], p * 0.9995), 2)
        d += datetime.timedelta(days=1)
    return bars


class DemoClient:
    """Mirrors RobinhoodClient's surface with canned, self-consistent data."""

    DEMO_ACCOUNT = "DEMO4242"
    # Positions mirror the CLUB's latest decided basket (owner call: a demo
    # account should hold what the club holds, not four hardcoded names). On
    # the hosted demo that basket comes from the mirrored REAL club; a local
    # sandbox falls back to its own decided sessions, then to a static list so
    # a fresh DB still renders. Quantities are hash-derived and deterministic.
    _FALLBACK = [("AAPL", 0.35), ("NVDA", 0.12), ("MSFT", 0.18), ("AMZN", 0.25)]

    # Every demo account is a standard $1,000 (owner call). The member's OWN
    # committed stake decides how much of it sits in the club basket vs cash:
    # commit 40% -> ~$400 across the basket names, ~$600 cash. No stake yet ->
    # all cash, so the stake step reads a clean "$1,000.00 · $1,000 cash".
    DEMO_TOTAL = 1000.0

    def _stake_fraction(self) -> float:
        """The member's committed slice of the $1,000, 0.0-1.0. Unknown member
        or no stake -> 0 (all cash) — never invent a position they didn't ask for.

        A stake buys in only at the NEXT cycle, like the real club: committing
        mid-cycle leaves you in cash until a vote session DECIDES (closes) after
        the commit — the demo rolls its session daily (voting.demo_roll_session),
        so a new member sees positions after the next roll, not the instant they
        pick a number. member_settings.updated_at is the commit time."""
        if not self.member_id:
            return 0.0
        try:
            from ..db import db as _db
            with _db() as c:
                r = c.execute("SELECT alloc_type, alloc_value, updated_at FROM member_settings "
                              "WHERE member_id=?", (self.member_id,)).fetchone()
                if not r or not r["alloc_value"]:
                    return 0.0
                # the first cycle decided AFTER the stake was committed is the
                # buy-in; no such session yet -> still all cash
                decided = c.execute(
                    "SELECT 1 FROM vote_sessions WHERE status IN ('closed','executed') "
                    "AND COALESCE(closed_at, opened_at, 0) > ? LIMIT 1",
                    (int(r["updated_at"] or 0),)).fetchone()
            if not decided:
                return 0.0
            if r["alloc_type"] == "dollar":
                return max(0.0, min(1.0, float(r["alloc_value"]) / self.DEMO_TOTAL))
            return max(0.0, min(1.0, float(r["alloc_value"]) / 100.0))
        except Exception:  # noqa: BLE001
            return 0.0

    @property
    def _HOLDINGS(self):  # noqa: N802 — name kept, call sites unchanged
        syms: list[str] = []
        try:
            from ..services import clubmirror
            rows = clubmirror.history(1)
            if rows and rows[0].get("basket"):
                syms = list(rows[0]["basket"])
        except Exception:  # noqa: BLE001
            pass
        if not syms:
            try:
                import json as _json

                from ..db import db as _db
                with _db() as c:
                    r = c.execute(
                        "SELECT picks_json FROM vote_sessions WHERE picks_json "
                        "IS NOT NULL ORDER BY trade_date DESC LIMIT 1").fetchone()
                if r:
                    syms = [p.get("symbol") for p in _json.loads(r["picks_json"] or "[]")
                            if p.get("symbol")]
            except Exception:  # noqa: BLE001
                pass
        invested = self.DEMO_TOTAL * self._stake_fraction()
        if not syms or invested <= 0:
            return []
        # Equal slice of the INVESTED amount per basket name. Quantities are
        # frozen against the BASE price (no hourly wiggle): sizing from the
        # current price re-fit every position on every read and pinned the
        # account value flat forever — fixed shares × a moving price is what
        # a real account is, so the value drifts hour to hour around the
        # invested amount while the rest stays cash.
        syms = syms[:12]
        per = invested / len(syms)
        return [(sym, round(per / _base_price(sym), 4)) for sym in syms]

    def __init__(self, member_id: int | None = None):
        self.member_id = member_id

    # ── read ──────────────────────────────────────────────────────────────
    def get_accounts(self) -> dict:
        return {"data": {"accounts": [
            {"account_number": self.DEMO_ACCOUNT, "agentic_allowed": True}]}}

    def get_portfolio(self, account_number: str) -> dict:
        holds = self._HOLDINGS
        held = sum(q * _price(s) for s, q in holds)
        # Cash is what the stake left uninvested (at base prices, so it does
        # not wobble); market drift lives entirely in the held slice.
        at_base = sum(q * _base_price(s) for s, q in holds)
        cash = round(max(0.0, self.DEMO_TOTAL - at_base), 2)
        return {"data": {"total_value": round(held + cash, 2), "cash": cash,
                         "buying_power": {"buying_power": cash}}}

    def get_positions(self, account_number: str) -> dict:
        return {"data": {"positions": [
            {"symbol": s, "type": "long", "quantity": q,
             "average_buy_price": round(_price(s) * 0.94, 2)}
            for s, q in self._HOLDINGS]}}

    def get_quotes(self, symbols: list[str]) -> dict:
        return {"data": {"results": [
            {"quote": {"symbol": s.upper(), "last_trade_price": _price(s.upper()),
                       # ±3% deterministic gap so change-since-close renders
                       "previous_close": round(_price(s.upper())
                                               * (1 - (_seed(s.upper()) % 61 - 30) / 1000), 2)}}
            for s in symbols]}}

    def get_equity_orders(self, account_number: str, cursor: str | None = None,
                          placed_agent: str | None = None) -> dict:
        return {"data": {"orders": [], "next": None}}

    def get_equity_historicals(self, symbols: list[str], start_time: str,
                               interval: str = "day", bounds: str | None = None,
                               end_time: str | None = None) -> dict:
        """Deterministic bars from start_time to now, HONOURING `interval`.

        An intraday interval (minute/5minute/30minute/hour/…) produces real
        regular-hours intraday bars via _intraday_bars, so DEMO_MODE renders a
        genuine 1D/1W chart instead of a step function of daily closes. Any
        other interval takes the daily generator below. `bounds` is accepted
        and ignored — the demo only ever models the regular session.
        """
        step = _INTRADAY_STEP_S.get(str(interval))
        if step:
            start_ts = _parse_ts(start_time, default=time.time() - 86400)
            end_ts = min(_parse_ts(end_time, default=time.time()), int(time.time()))
            return {"data": {"results": [
                {"symbol": s.upper(),
                 "bars": _intraday_bars(s.upper(), start_ts, end_ts, step)}
                for s in symbols]}}
        return self._daily_historicals(symbols, start_time)

    def _daily_historicals(self, symbols: list[str], start_time: str) -> dict:
        """Deterministic daily closes from start_time to today (weekdays only).

        Each bar is a pure function of (symbol, date): a gentle drift from ~80%
        of the symbol's pseudo-price up to it, plus a ±4.5% per-day hash wiggle —
        so the stock-detail range charts (and demo club performance) render with
        plausible, refresh-stable data instead of an empty series. Bars carry
        full OHLCV like the broker's do: the detail payload's day range is read
        off the last one.
        """
        try:
            start = datetime.date.fromisoformat(str(start_time)[:10])
        except ValueError:
            start = datetime.date.today() - datetime.timedelta(days=365)
        today = datetime.date.today()
        span = max((today - start).days, 1)
        results = []
        for s in symbols:
            sym = s.upper()
            p = _price(sym)
            bars = []
            d = start
            while d <= today:
                if d.weekday() < 5:                    # trading days only
                    wig = int(hashlib.sha256(f"{sym}:{d}".encode()).hexdigest()[:6], 16)
                    trend = 0.80 + 0.20 * (d - start).days / span
                    close = p * trend * (1 + (wig % 900 - 450) / 10000)
                    lo = close * (1 - (wig % 40 + 5) / 2000)   # 0.25%–2.2% below
                    hi = close * (1 + ((wig >> 5) % 40 + 5) / 2000)
                    if d == today:
                        # the live quote is _price(sym) and moves on the hour —
                        # today's bar must still contain it, or the demo's day
                        # range renders with the price outside its own low/high
                        lo, hi = min(lo, p * 0.995), max(hi, p * 1.005)
                    bars.append({
                        "begins_at": d.isoformat(),
                        "open_price": round((lo + hi) / 2, 2),
                        "close_price": round(close, 2),
                        "high_price": round(hi, 2),
                        "low_price": round(lo, 2),
                        "volume": 800_000 + wig % 40_000_000,
                    })
                d += datetime.timedelta(days=1)
            results.append({"symbol": sym, "bars": bars})
        return {"data": {"results": results}}

    def get_equity_fundamentals(self, symbols: list[str]) -> dict:
        """Plausible per-symbol fundamentals, deterministically derived from
        _price(symbol) — the demo stock-detail page must render fully."""
        results = []
        for s in symbols:
            sym = s.upper()
            p = _price(sym)
            seed = _seed(sym)
            shares = (200 + seed % 9800) * 1_000_000    # 200M–10B shares outstanding
            results.append({"symbol": sym, "fundamentals": {
                "market_cap": round(p * shares, 2),
                "pe_ratio": round(8 + (seed % 320) / 10, 2),
                "dividend_yield": round((seed % 300) / 100, 2),
                "high_52_weeks": round(p * 1.22, 2),
                "low_52_weeks": round(p * 0.74, 2),
                "volume": 500_000 + seed % 20_000_000,
                "average_volume": 750_000 + (seed >> 3) % 15_000_000,
                "sector": _SECTORS[seed % len(_SECTORS)],
                "description": f"{sym} is a demo-mode company. All figures on this "
                               f"page are deterministic fakes derived from the symbol "
                               f"— no market data was harmed.",
            }})
        return {"data": {"results": results}}

    def get_watchlists(self) -> dict:
        return {"data": {"results": []}}

    def get_watchlist_items(self, list_id: str) -> dict:
        return {"data": {"results": []}}

    # ── trade (reviews always clean; places echo back a fake accepted order) ─
    def review_market_buy(self, account_number: str, symbol: str, dollar_amount: str) -> dict:
        return {"data": {"order_checks": {}}}

    def review_market_sell(self, account_number: str, symbol: str, quantity: str) -> dict:
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, account_number: str, symbol: str, dollar_amount: str,
                         ref_id: str | None = None) -> dict:
        return {"data": {"order": {"symbol": symbol, "side": "buy", "state": "demo",
                                   "dollar_amount": dollar_amount, "ref_id": ref_id}}}

    def place_market_sell(self, account_number: str, symbol: str, quantity: str,
                          ref_id: str | None = None) -> dict:
        return {"data": {"order": {"symbol": symbol, "side": "sell", "state": "demo",
                                   "quantity": quantity, "ref_id": ref_id}}}
