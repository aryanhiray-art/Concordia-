"""Alpaca Trading API client — the reference account's broker.

Why this exists: a member's Robinhood account holds personal positions, idle
cash and gifted shares alongside the club's, and Robinhood does not reliably
echo our `ref_id` back, so `positions.club_shares_derived` has to net the whole
order feed by symbol and can never separate a personal AAPL share from a club
one. In a dedicated Alpaca account owned by the operator there is nothing to
separate: every fill is the club's, every order carries our own
`client_order_id`, and the activities feed reports real cash movements instead
of the 25%-jump heuristic clubnav uses today.

Read-only by default. `submit_order` exists but the reference service refuses to
call it unless BOTH reference_enabled and reference_armed are set — arming is a
human action, never a deploy's.
"""
from __future__ import annotations

import requests

from ..config import settings

_PAPER = "https://paper-api.alpaca.markets"
_DATA = "https://data.alpaca.markets"
_LIVE = "https://api.alpaca.markets"
_TIMEOUT = 20


class AlpacaError(RuntimeError):
    """Base error for Alpaca transport failures."""


class AlpacaAuthError(AlpacaError):
    """401/403 — key id/secret wrong, or a paper key pointed at the live host."""


class AlpacaTransientError(AlpacaError):
    """Timeout / connection failure / 429 / 5xx — safe to retry later."""


class AlpacaClient:
    def __init__(self, key_id: str | None = None, secret: str | None = None,
                 paper: bool | None = None):
        self.key_id = (key_id if key_id is not None else settings.alpaca_key_id).strip()
        self.secret = (secret if secret is not None else settings.alpaca_secret).strip()
        self.paper = settings.alpaca_paper if paper is None else bool(paper)
        self.base = _PAPER if self.paper else _LIVE
        if not self.key_id or not self.secret:
            raise AlpacaAuthError("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set")

    # ── transport ────────────────────────────────────────────────────────────
    def _headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "accept": "application/json"}

    def _request(self, method: str, path: str, **kw):
        try:
            r = requests.request(method, f"{self.base}{path}", headers=self._headers(),
                                 timeout=_TIMEOUT, **kw)
        except requests.RequestException as e:
            raise AlpacaTransientError(f"{type(e).__name__}: {str(e)[:160]}") from e
        if r.status_code in (401, 403):
            where = "paper" if self.paper else "live"
            raise AlpacaAuthError(f"HTTP {r.status_code} from the {where} host — check the key "
                                  f"pair and that ALPACA_PAPER matches which key you issued")
        if r.status_code == 429 or r.status_code >= 500:
            raise AlpacaTransientError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise AlpacaError(f"HTTP {r.status_code}: {r.text[:300]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError as e:
            raise AlpacaError(f"non-JSON response: {r.text[:200]}") from e

    def _get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params or None)

    # ── reads ────────────────────────────────────────────────────────────────
    def account(self) -> dict:
        """Account status, equity, cash, buying power. Also the connectivity probe."""
        return self._get("/v2/account") or {}

    def positions(self) -> list[dict]:
        return self._get("/v2/positions") or []

    def orders(self, status: str = "all", limit: int = 100,
               after: str | None = None) -> list[dict]:
        p = {"status": status, "limit": min(int(limit), 500), "direction": "desc"}
        if after:
            p["after"] = after
        return self._get("/v2/orders", p) or []

    def activities(self, activity_type: str | None = None,
                   after: str | None = None, page_size: int = 100) -> list[dict]:
        """Fills (FILL), cash in/out (CSD/CSW), dividends (DIV), and the rest.

        This is the feed clubnav cannot have on Robinhood: a real record of when
        money moved, so a time-weighted return no longer has to GUESS at a
        deposit by watching for a >25% single-day jump.
        """
        path = f"/v2/account/activities/{activity_type}" if activity_type else "/v2/account/activities"
        p: dict = {"page_size": min(int(page_size), 100)}
        if after:
            p["after"] = after
        return self._get(path, p) or []

    def portfolio_history(self, period: str = "all", timeframe: str = "1D",
                          extended_hours: bool = False) -> dict:
        """The account's equity curve, straight from the broker.

        Replaces sampling an account value ourselves — which is exactly how the
        club's first seven weeks ended up as weekly SUNDAY readings with the
        benchmark pinned flat.
        """
        return self._get("/v2/account/portfolio/history",
                         {"period": period, "timeframe": timeframe,
                          "extended_hours": str(bool(extended_hours)).lower()}) or {}

    def daily_closes(self, symbol: str, start: str) -> dict[str, float]:
        """{YYYY-MM-DD: close} daily bars, from Alpaca's own market data.

        Deliberately NOT borrowed from a member's Robinhood client: the point of
        the reference account is that its record survives independently. If the
        Robinhood connection is ever cut, this series keeps running.
        """
        out: dict[str, float] = {}
        token: str | None = None
        for _ in range(20):                       # bounded pagination
            p = {"symbols": symbol.upper(), "timeframe": "1Day", "start": start,
                 "limit": 10000, "adjustment": "split"}
            if token:
                p["page_token"] = token
            try:
                r = requests.get(f"{_DATA}/v2/stocks/bars", headers=self._headers(),
                                 params=p, timeout=_TIMEOUT)
            except requests.RequestException as e:
                raise AlpacaTransientError(f"{type(e).__name__}: {str(e)[:160]}") from e
            if r.status_code in (401, 403):
                raise AlpacaAuthError(f"HTTP {r.status_code} from the data API")
            if r.status_code == 429 or r.status_code >= 500:
                raise AlpacaTransientError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                raise AlpacaError(f"HTTP {r.status_code}: {r.text[:300]}")
            body = r.json() or {}
            for b in (body.get("bars") or {}).get(symbol.upper(), []):
                t, c = b.get("t"), b.get("c")
                if t and c is not None:
                    out[str(t)[:10]] = float(c)
            token = body.get("next_page_token")
            if not token:
                break
        return out

    def snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """{SYMBOL: snapshot} from Alpaca market data — latest trade plus the
        current and previous daily bars, one request for the whole list.

        Same reasoning as daily_closes for living on this client rather than a
        member's Robinhood one: public market data must never ride on a
        member's borrowed broker session.
        """
        syms = ",".join(s.upper() for s in symbols)
        try:
            r = requests.get(f"{_DATA}/v2/stocks/snapshots", headers=self._headers(),
                             params={"symbols": syms}, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise AlpacaTransientError(f"{type(e).__name__}: {str(e)[:160]}") from e
        if r.status_code in (401, 403):
            raise AlpacaAuthError(f"HTTP {r.status_code} from the data API")
        if r.status_code == 429 or r.status_code >= 500:
            raise AlpacaTransientError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise AlpacaError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            body = r.json() or {}
        except ValueError as e:
            raise AlpacaError(f"non-JSON response: {r.text[:200]}") from e
        # The multi-symbol endpoint keys the payload by symbol at the top
        # level; tolerate the {"snapshots": {...}} wrapper some API revisions
        # use so an upstream version bump degrades to "no quotes", not a crash.
        if isinstance(body.get("snapshots"), dict):
            body = body["snapshots"]
        return {k.upper(): v for k, v in body.items() if isinstance(v, dict)}

    def clock(self) -> dict:
        return self._get("/v2/clock") or {}

    # ── write (guarded by the caller — see services/reference.py) ────────────
    def submit_order(self, symbol: str, *, notional: float | None = None,
                     qty: float | None = None, side: str = "buy",
                     client_order_id: str | None = None,
                     time_in_force: str = "day") -> dict:
        """Place ONE market order. Callers MUST gate on settings.reference_armed.

        `client_order_id` is the whole point of this account: Alpaca echoes it
        back on the order and in the activities feed, so a fill is attributable
        to the exact (session, symbol) that caused it — the round trip Robinhood
        does not reliably make.
        """
        if (notional is None) == (qty is None):
            raise AlpacaError("pass exactly one of notional= or qty=")
        body: dict = {"symbol": symbol.upper(), "side": side, "type": "market",
                      "time_in_force": time_in_force}
        if notional is not None:
            body["notional"] = str(round(float(notional), 2))
        else:
            body["qty"] = str(qty)
        if client_order_id:
            body["client_order_id"] = client_order_id[:128]
        return self._request("POST", "/v2/orders", json=body) or {}


def probe() -> dict:
    """{reachable, detail, ...} — never raises. Used by the admin panel."""
    if not settings.alpaca_configured:
        return {"reachable": False, "configured": False,
                "detail": "ALPACA_KEY_ID / ALPACA_SECRET_KEY not set"}
    try:
        a = AlpacaClient().account()
    except AlpacaError as e:
        return {"reachable": False, "configured": True,
                "detail": f"{type(e).__name__}: {e}"}
    return {"reachable": True, "configured": True,
            "paper": settings.alpaca_paper,
            "account_number": a.get("account_number"),
            "status": a.get("status"),
            "currency": a.get("currency"),
            "equity": a.get("equity"),
            "cash": a.get("cash"),
            "buying_power": a.get("buying_power"),
            "shorting_enabled": a.get("shorting_enabled"),
            "pattern_day_trader": a.get("pattern_day_trader"),
            "detail": "ok"}
