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
    if not place_json:
        return 0.0
    try:
        order = (json.loads(place_json).get("data") or {}).get("order") or {}
    except Exception:  # noqa: BLE001
        return 0.0
    return _num(order.get("cumulative_quantity")) or _num(order.get("quantity")) or 0.0


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
