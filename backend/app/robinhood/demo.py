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

import hashlib
import time


def _price(symbol: str) -> float:
    """Stable pseudo-price per symbol: $20–$520, varies gently by the hour."""
    base = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 50000 / 100 + 20
    wiggle = (int(time.time() // 3600) + len(symbol)) % 7 - 3
    return round(base * (1 + wiggle / 200), 2)


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
            {"quote": {"symbol": s.upper(), "last_trade_price": _price(s.upper())}}
            for s in symbols]}}

    def get_equity_orders(self, account_number: str, cursor: str | None = None,
                          placed_agent: str | None = None) -> dict:
        return {"data": {"orders": [], "next": None}}

    def get_equity_historicals(self, symbols: list[str], start_time: str,
                               interval: str = "day") -> dict:
        return {"data": {"results": []}}

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
