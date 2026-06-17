"""What the *club* holds for a member, vs. what's in their account overall.

The rebalance must only ever touch positions the club itself bought — never a
member's own holdings. We reconstruct club holdings from placed executions:
net shares per symbol = sum(placed buys) - sum(placed sells). Live account
positions are read separately and only used for the one-time full reset.
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


def _filled_qty(place_json: str | None) -> float:
    """Shares from a placed order.

    A market order's *synchronous* place response is the just-accepted order, so
    cumulative_quantity is 0 and a dollar-denominated buy carries no quantity yet.
    The executor therefore stamps an estimate (`_concord_est_qty` = dollars/price
    for buys, share count for sells) at placement time; we prefer a real filled
    quantity when present and fall back to that estimate.
    """
    if not place_json:
        return 0.0
    try:
        payload = json.loads(place_json)
    except Exception:  # noqa: BLE001
        return 0.0
    order = (payload.get("data") or {}).get("order") or {}
    real = _num(order.get("cumulative_quantity")) or _num(order.get("quantity")) or 0.0
    if real > 0:
        return real
    return _num(payload.get("_concord_est_qty")) or 0.0


def club_shares(member_id: int) -> dict[str, float]:
    """Net shares the club currently holds for this member, per symbol (>0 only)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, side, place_json FROM executions "
            "WHERE member_id=? AND status='placed'", (member_id,)
        ).fetchall()
    shares: dict[str, float] = {}
    for r in rows:
        q = _filled_qty(r["place_json"])
        if not q:
            continue
        sign = -1.0 if r["side"] == "sell" else 1.0
        shares[r["symbol"]] = shares.get(r["symbol"], 0.0) + sign * q
    return {s: q for s, q in shares.items() if q > 1e-9}


def holdings_with_value(member_id: int) -> list[dict]:
    """The member's long positions valued at the live quote: [{symbol, quantity, value, pnl}]."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return []
    try:
        client = vault.ensure_client(member_id)
        pos = (client.get_positions(acct).get("data") or {}).get("positions") or []
    except Exception:  # noqa: BLE001
        return []
    longs = [p for p in pos if p.get("type") == "long" and (_num(p.get("quantity")) or 0) > 1e-9]
    syms = [p["symbol"] for p in longs if p.get("symbol")]
    quotes: dict[str, float] = {}
    for i in range(0, len(syms), 20):
        try:
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
            for e in res:
                q = e.get("quote") or {}
                if q.get("symbol"):
                    quotes[q["symbol"]] = _num(q.get("last_trade_price"))
        except Exception:  # noqa: BLE001
            pass
    out = []
    for p in longs:
        qty = _num(p.get("quantity")) or 0.0
        px = quotes.get(p["symbol"])
        avg = _num(p.get("average_buy_price")) or px or 0.0
        val = (px if px is not None else avg) * qty
        out.append({"symbol": p["symbol"], "quantity": round(qty, 4), "value": round(val, 2),
                    "pnl": round(((px - avg) * qty), 2) if px is not None else None})
    out.sort(key=lambda h: h["value"], reverse=True)
    return out


def live_positions(member_id: int) -> list[dict]:
    """All long positions in the member's account: [{symbol, quantity}]. Best-effort."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return []
    try:
        client = vault.ensure_client(member_id)
        pos = (client.get_positions(acct).get("data") or {}).get("positions") or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in pos:
        q = _num(p.get("quantity")) or 0.0
        if p.get("type") == "long" and q > 1e-9 and p.get("symbol"):
            out.append({"symbol": p["symbol"], "quantity": q})
    return out
