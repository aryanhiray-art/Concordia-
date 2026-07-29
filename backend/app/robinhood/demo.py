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


def _price(symbol: str) -> float:
    """Stable pseudo-price per symbol: $20–$520, varies gently by the hour."""
    base = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 50000 / 100 + 20
    wiggle = (int(time.time() // 3600) + len(symbol)) % 7 - 3
    return round(base * (1 + wiggle / 200), 2)


def _seed(symbol: str) -> int:
    """Deterministic per-symbol integer for fundamentals fakes (no time wiggle)."""
    return int(hashlib.sha256(symbol.encode()).hexdigest()[8:16], 16)


_SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials",
            "Consumer Discretionary", "Energy", "Utilities", "Communication Services"]


class DemoClient:
    """Mirrors RobinhoodClient's surface with canned, self-consistent data."""

    DEMO_ACCOUNT = "DEMO4242"
    _HOLDINGS = [("AAPL", 0.35), ("NVDA", 0.12), ("MSFT", 0.18), ("AMZN", 0.25)]

    def __init__(self, member_id: int | None = None):
        self.member_id = member_id

    # ── read ──────────────────────────────────────────────────────────────
    def get_accounts(self) -> dict:
        return {"data": {"accounts": [
            {"account_number": self.DEMO_ACCOUNT, "agentic_allowed": True}]}}

    def get_portfolio(self, account_number: str) -> dict:
        held = sum(q * _price(s) for s, q in self._HOLDINGS)
        return {"data": {"total_value": round(held + 100.0, 2), "cash": 100.0,
                         "buying_power": {"buying_power": 100.0}}}

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
                               interval: str = "day") -> dict:
        """Deterministic daily closes from start_time to today (weekdays only).

        Each bar is a pure function of (symbol, date): a gentle drift from ~80%
        of the symbol's pseudo-price up to it, plus a ±4.5% per-day hash wiggle —
        so the stock-detail range charts (and demo club performance) render with
        plausible, refresh-stable data instead of an empty series.
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
                    bars.append({"begins_at": d.isoformat(), "close_price": round(close, 2)})
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
