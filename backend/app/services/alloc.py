"""Per-member allocation, agentic-account balance, and club-only P&L.

A member commits a slice of their Robinhood account to the club — either a
percentage of buying power or a fixed dollar amount. That committed amount is
their stake (= voting power) and the budget the executor deploys. Profit is
tracked ONLY on what the club actually bought (from placed executions), never
the member's whole account.
"""
from __future__ import annotations

import json
import time

from ..config import settings
from ..db import db, kv_get, kv_set
from ..robinhood import vault

_READY_TTL = 180  # seconds to cache a trade-readiness probe (avoids RH rate limits)


def trade_readiness(member_id: int, force: bool = False) -> dict:
    """Can this member's account actually place a buy right now?

    Robinhood blocks buys on an agentic account that hasn't completed its
    investing-goals/suitability questionnaire — the review returns an
    EQUITY_SUITABILITY alert and the place 400s. We probe with a tiny $1 review
    (no order placed) and cache the verdict so onboarding can guide the member.
    """
    acct = vault.account_number_for(member_id)
    if not acct:
        return {"connected": False, "ready": False, "reason": "not_connected"}
    ck = f"trade_ready:{member_id}"
    if not force:
        cached = kv_get(ck)
        if cached:
            try:
                c = json.loads(cached)
                if time.time() - c.get("ts", 0) < _READY_TTL:
                    return {k: v for k, v in c.items() if k != "ts"}
            except Exception:  # noqa: BLE001
                pass
    out = {"connected": True, "ready": False, "reason": "unknown"}
    try:
        client = vault.ensure_client(member_id)
        rv = client.review_market_buy(acct, "AAPL", "1.00")
        checks = (rv.get("data") or rv).get("order_checks")
        atype = checks.get("alertType") if isinstance(checks, dict) else None
        if atype == "EQUITY_SUITABILITY":
            out = {"connected": True, "ready": False, "reason": "needs_questionnaire"}
        elif checks:
            out = {"connected": True, "ready": False, "reason": "blocked", "detail": str(checks)[:140]}
        else:
            out = {"connected": True, "ready": True, "reason": "ok"}
    except Exception as e:  # noqa: BLE001
        out = {"connected": True, "ready": False, "reason": "probe_failed", "detail": str(e)[:140]}
    kv_set(ck, json.dumps({**out, "ts": int(time.time())}))
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def member_balance(member_id: int) -> dict | None:
    """Live agentic-account balance, or None if not connected/reachable."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return None
    try:
        client = vault.ensure_client(member_id)
        port = (client.get_portfolio(acct).get("data") or {})
    except Exception:  # noqa: BLE001
        return None
    bp = port.get("buying_power") or {}
    return {
        "total_value": _num(port.get("total_value")),
        "cash": _num(port.get("cash")),
        "buying_power": _num(bp.get("buying_power") if isinstance(bp, dict) else bp),
    }


def resolve_allocation(alloc_type: str | None, alloc_value: float | None,
                       buying_power: float | None) -> float:
    """The committed $ for this member: % of buying power, or a fixed $, capped at BP."""
    bp = buying_power or 0.0
    v = alloc_value or 0.0
    if alloc_type == "dollar":
        amt = v
    else:  # percent (default)
        amt = bp * (v / 100.0)
    return round(max(0.0, min(amt, bp)), 2)


def rebalance_budget(alloc_type: str | None, alloc_value: float | None,
                     total_value: float | None, buying_power: float | None,
                     daily_cap_usd: float | None = None) -> float:
    """Target $ of the basket to HOLD — the committed stake, not free cash.

    Unlike resolve_allocation (which caps at buying power for an initial buy), the
    rebalance frees cash by selling, so the target must be the full committed
    stake even when it's already deployed in positions. For a $ stake that's the
    committed dollars (capped at total account value); for % it's % of equity.

    Hard caps (safety, always applied): the global MAX_NOTIONAL_PER_MEMBER
    ceiling from config, and — when the caller passes the member's
    `daily_cap_usd` (>0 means set; None/0 means no cap) — that per-member cap.
    """
    tv = total_value or 0.0
    v = alloc_value or 0.0
    if alloc_type == "dollar":
        target = min(v, tv)
    else:  # percent (default)
        target = tv * (v / 100.0)
    target = min(target, settings.max_notional_per_member)
    if daily_cap_usd is not None and daily_cap_usd > 0:
        target = min(target, daily_cap_usd)
    return round(max(0.0, target), 2)


def member_allocation(member_id: int) -> dict:
    """Current allocation setting + the live $ it resolves to (needs a fresh balance)."""
    with db() as conn:
        row = conn.execute(
            "SELECT alloc_type, alloc_value FROM member_settings WHERE member_id=?", (member_id,)
        ).fetchone()
    bal = member_balance(member_id)
    bp = (bal or {}).get("buying_power")
    tv = (bal or {}).get("total_value")
    atype = (row["alloc_type"] if row else "percent")
    aval = (row["alloc_value"] if row else 0.0)
    return {
        "type": atype,
        "value": aval,
        # Resolve against TOTAL account value via rebalance_budget — the same
        # figure the executor + voting power use — NOT resolve_allocation, which
        # caps at buying power and made a deployed $100 stake read as ~$5 on the
        # dashboard while voting power correctly showed ~$100 (finding #18).
        "resolved_usd": rebalance_budget(atype, aval, tv, bp),
        "balance": bal,
    }


def club_pnl(member_id: int) -> dict:
    """Unrealized P&L on the club's CURRENTLY-HELD shares for this member.

    Shares = the sign-aware, fill-based net from positions.club_shares (buys −
    sells, real fills preferred over the estimate). Cost basis + market value use
    the broker's average buy price and the live quote, so a rotated-out name
    doesn't distort the number. (finding #7 — the old version summed SELL share
    quantities with NO sign, grossly misreporting P&L.)
    """
    from . import positions
    shares = positions.club_shares(member_id)   # club-only, sign-aware, fill-based
    if not shares:
        return {"invested": 0.0, "value": 0.0, "profit": 0.0, "profit_pct": None, "positions": 0}
    avg: dict[str, float] = {}
    last: dict[str, float] = {}
    try:
        acct = vault.account_number_for(member_id)
        client = vault.ensure_client(member_id)
        pos = (client.get_positions(acct).get("data") or {}).get("positions") or []
        for p in pos:
            s = p.get("symbol")
            if s:
                avg[s] = _num(p.get("average_buy_price")) or 0.0
        syms = list(shares)
        for i in range(0, len(syms), 20):
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
            for e in res:
                q = e.get("quote") or {}
                if q.get("symbol"):
                    last[q["symbol"]] = _num(q.get("last_trade_price")) or 0.0
    except Exception:  # noqa: BLE001
        pass
    invested = sum(qty * (avg.get(sym) or last.get(sym) or 0.0) for sym, qty in shares.items())
    value = sum(qty * (last.get(sym) or avg.get(sym) or 0.0) for sym, qty in shares.items())
    profit = value - invested
    return {
        "invested": round(invested, 2),
        "value": round(value, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit / invested * 100, 2) if invested else None,
        "positions": len(shares),
    }
