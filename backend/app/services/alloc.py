"""Per-member allocation, agentic-account balance, and club-only P&L.

A member commits a slice of their Robinhood account to the club — either a
percentage of buying power or a fixed dollar amount. That committed amount is
their stake (= voting power) and the budget the executor deploys. Profit is
tracked ONLY on what the club actually bought (from placed executions), never
the member's whole account.
"""
from __future__ import annotations

import json

from ..db import db
from ..robinhood import vault


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
                     total_value: float | None, buying_power: float | None) -> float:
    """Target $ of the basket to HOLD — the committed stake, not free cash.

    Unlike resolve_allocation (which caps at buying power for an initial buy), the
    rebalance frees cash by selling, so the target must be the full committed
    stake even when it's already deployed in positions. For a $ stake that's the
    committed dollars (capped at total account value); for % it's % of equity.
    """
    tv = total_value or 0.0
    v = alloc_value or 0.0
    if alloc_type == "dollar":
        return round(min(v, tv), 2)
    return round(tv * (v / 100.0), 2)


def member_allocation(member_id: int) -> dict:
    """Current allocation setting + the live $ it resolves to (needs a fresh balance)."""
    with db() as conn:
        row = conn.execute(
            "SELECT alloc_type, alloc_value FROM member_settings WHERE member_id=?", (member_id,)
        ).fetchone()
    bal = member_balance(member_id)
    bp = (bal or {}).get("buying_power")
    atype = (row["alloc_type"] if row else "percent")
    aval = (row["alloc_value"] if row else 0.0)
    return {
        "type": atype,
        "value": aval,
        "resolved_usd": resolve_allocation(atype, aval, bp),
        "balance": bal,
    }


def club_pnl(member_id: int) -> dict:
    """Profit tracked ONLY on club-placed orders: current value vs invested.

    Sums the shares + cost the executor actually bought for this member (placed
    executions), values them at current quotes. Zero until live trades exist.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, dollar_amount, place_json FROM executions "
            "WHERE member_id=? AND status='placed'", (member_id,)
        ).fetchall()
    invested = 0.0
    shares: dict[str, float] = {}
    for r in rows:
        invested += _num(r["dollar_amount"]) or 0.0
        try:
            order = (json.loads(r["place_json"]).get("data") or {}).get("order") or {}
            q = _num(order.get("cumulative_quantity")) or _num(order.get("quantity")) or 0.0
        except Exception:  # noqa: BLE001
            q = 0.0
        shares[r["symbol"]] = shares.get(r["symbol"], 0.0) + q
    if not shares:
        return {"invested": round(invested, 2), "value": 0.0, "profit": 0.0, "profit_pct": None, "positions": 0}
    value = 0.0
    try:
        acct = vault.account_number_for(member_id)
        client = vault.ensure_client(member_id)
        syms = [s for s, q in shares.items() if q]
        quotes: dict[str, float] = {}
        for i in range(0, len(syms), 20):
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
            for e in res:
                q = e.get("quote") or {}
                if q.get("symbol"):
                    quotes[q["symbol"]] = _num(q.get("last_trade_price")) or 0.0
        value = sum(qty * quotes.get(sym, 0.0) for sym, qty in shares.items())
    except Exception:  # noqa: BLE001
        value = 0.0
    profit = value - invested
    return {
        "invested": round(invested, 2),
        "value": round(value, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit / invested * 100, 2) if invested else None,
        "positions": len([s for s, q in shares.items() if q]),
    }
