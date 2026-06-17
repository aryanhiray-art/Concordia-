"""Shared market-data helper.

Bots have no Robinhood account, but scoring their picks (and showing prices in
their prompt) needs live quotes. We borrow any connected member's RH client to
read quotes — read-only, never trades. Falls back to {} when nobody's connected.
"""
from __future__ import annotations

from ..db import db
from ..robinhood import vault


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _any_connected_member() -> int | None:
    with db() as conn:
        row = conn.execute(
            "SELECT member_id FROM rh_accounts WHERE status='connected' "
            "ORDER BY connected_at DESC LIMIT 1"
        ).fetchone()
    return row["member_id"] if row else None


def quotes(symbols: list[str]) -> dict[str, float]:
    """{symbol: last_trade_price} for as many as we can fetch. {} if no client."""
    syms = sorted({s.upper() for s in symbols if s})
    if not syms:
        return {}
    mid = _any_connected_member()
    if mid is None:
        return {}
    try:
        client = vault.ensure_client(mid)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for i in range(0, len(syms), 20):
        try:
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
        except Exception:  # noqa: BLE001
            continue
        for e in res:
            q = e.get("quote") or e
            sym = (q.get("symbol") or "").upper()
            px = _num(q.get("last_trade_price")) or _num(q.get("price"))
            if sym and px:
                out[sym] = px
    return out
