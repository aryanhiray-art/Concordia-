"""What the *club* holds for a member, vs. what's in their account overall.

The rebalance must only ever touch positions the club itself bought — never a
member's own holdings. We reconstruct club holdings from placed executions:
net shares per symbol = sum(placed buys) - sum(placed sells). Live account
positions are read separately and only used for the one-time full reset.
"""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

from ..db import audit, db
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


# ─── feed-DERIVED club position (re-architecture: anchor sizing to broker reality) ──
# club_shares (above) is a cumulative sum of placement-time ESTIMATES over all
# history, never re-anchored to the account → it drifted 16-90x high. The functions
# below derive the club position from the broker's own AGENTIC ORDER FEED instead,
# so it can only under-count (never sell personal shares) and never drifts. Gated by
# settings.club_position_source ("ledger" default = unchanged; "derived" = cutover).

class DerivationIncomplete(RuntimeError):
    """The order feed could not be read to its end (read error / non-terminating
    pagination). The caller MUST NOT size or liquidate off a partial → fail closed
    (skip the member this cycle); never treat as an empty position."""


_FEED_TTL_S = 120                 # collapse the multiple feed reads within one cycle
_SAFETY_PAGE_CEILING = 500        # bounds a runaway cursor; hitting it == incomplete
_FEED_CACHE: dict[str, tuple[float, list[dict]]] = {}


def invalidate_feed_cache(account_number: str | None = None) -> None:
    """Drop one (or all) cached order feeds — for tests + an explicit post-liquidation re-read."""
    if account_number is None:
        _FEED_CACHE.clear()
    else:
        _FEED_CACHE.pop(account_number, None)


def _cursor_from_next(nxt) -> str | None:
    if not nxt:
        return None
    try:
        c = parse_qs(urlparse(str(nxt)).query).get("cursor")
        return c[0] if c else None
    except Exception:  # noqa: BLE001
        return None


def _normalize_orders(payload):
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        return (data.get("orders") or data.get("results") or []), data.get("next")
    if isinstance(data, list):
        return data, None
    return [], None


def _order_filled_qty(o: dict) -> float:
    """Real filled shares for an order — 0.0 if unfilled (rejected/cancelled/queued),
    so a dead order contributes exactly nothing and a cancelled-after-partial still
    counts its real partial."""
    q = _num(o.get("cumulative_quantity"))
    if q is not None:
        return q
    total, seen = 0.0, False
    for e in o.get("executions") or []:
        eq = _num(e.get("quantity"))
        if eq is not None:
            total += eq
            seen = True
    return total if seen else 0.0


def _fetch_full_order_feed(client, account_number: str) -> list[dict]:
    """EVERY equity order for the account (NO placed_agent filter — we must SEE 'user'
    orders to exclude them, and must never MISS a club sell that lost its tag), paged
    to next==null. Raises DerivationIncomplete on any read/pagination failure. Cached."""
    now = time.time()
    hit = _FEED_CACHE.get(account_number)
    if hit and now - hit[0] < _FEED_TTL_S:
        return hit[1]
    orders: list[dict] = []
    cursor, seen, pages = None, set(), 0
    while True:
        if pages >= _SAFETY_PAGE_CEILING:
            raise DerivationIncomplete("page ceiling hit before feed end")
        try:
            payload = client.get_equity_orders(account_number, cursor=cursor)
        except Exception as e:  # noqa: BLE001 — a partial feed is untrustworthy in both directions
            raise DerivationIncomplete(f"order read failed: {type(e).__name__}: {e}") from e
        page, nxt = _normalize_orders(payload)
        orders.extend(o for o in page if isinstance(o, dict))
        cursor = _cursor_from_next(nxt)
        if cursor is None:
            break
        if cursor in seen:
            raise DerivationIncomplete("cursor did not advance")
        seen.add(cursor)
        pages += 1
    _FEED_CACHE[account_number] = (now, orders)
    return orders


def _order_agent(o) -> str | None:
    a = o.get("placed_agent")
    return a if a in ("agentic", "user") else None      # unknown → None (ambiguous)


def club_position_from_orders(orders: list[dict]) -> dict[str, float]:
    """Net club shares per symbol from the order feed. ASYMMETRIC agent rule — every
    ambiguity pushes the count DOWN (the money-safe direction):
      BUY  counts ONLY placed_agent=='agentic'  (excludes 'user' AND None)
      SELL subtracts everything EXCEPT 'user'    (includes 'agentic' AND None)
    So we can never miss a club sell (over-count → sell personal) and never adopt a
    personal/ambiguous buy. Not yet clamped/intersected — the caller does that."""
    net: dict[str, float] = {}
    for o in orders:
        sym = str(o.get("symbol") or "").upper()
        side = str(o.get("side") or "").lower()
        if not sym or side not in ("buy", "sell"):
            continue
        qty = _order_filled_qty(o)
        if qty <= 0:
            continue
        agent = _order_agent(o)
        if side == "buy":
            if agent != "agentic":
                continue
            net[sym] = net.get(sym, 0.0) + qty
        else:
            if agent == "user":
                continue
            net[sym] = net.get(sym, 0.0) - qty
    return net


def _club_traded_symbols(member_id: int) -> set[str]:
    """Every symbol the club has ever PLACED — the club/personal boundary by symbol.
    A genuine club buy is always in `executions`, so intersecting the derived keys
    with this set never drops a real club symbol but DOES drop a feed mis-tag on a
    name the club never traded (the last personal-safety belt)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND status='placed'",
            (member_id,)).fetchall()
    return {r["symbol"] for r in rows}


def symbol_renames() -> dict[str, str]:
    """OLD -> NEW for every corporate action the club has migrated.

    The broker's order feed is IMMUTABLE history: after AvalonBay became
    Vivmark it still reports the original fills as AVB. services/renames.migrate
    renames the LEDGER rows to VMRK, so the derived position — which intersects
    feed symbols with ledger symbols — dropped the name from BOTH sides and the
    club could never sell it again. Folding the feed forward through this map is
    what keeps a renamed holding reachable.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT target FROM audit_log WHERE action='symbol_migrated' "
            "AND target LIKE '%->%'").fetchall()
    out: dict[str, str] = {}
    for r in rows:
        old, _, new = (r["target"] or "").partition("->")
        if old and new:
            out[old.strip().upper()] = new.strip().upper()
    return out


def corporate_action_shares(member_id: int) -> dict[str, float]:
    """Share deltas the club holds ONLY because of a corporate action.

    A conversion creates shares with no order behind them, so the feed cannot
    see them (AVB 0.026932 in the feed vs VMRK 0.0752 actually held). renames
    .migrate records the difference as a $0 execution tagged `corporate_action`;
    this reads those back so the derived position reconstructs the true holding.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, side, place_json FROM executions "
            "WHERE member_id=? AND status='placed' AND place_json LIKE '%corporate_action%'",
            (member_id,)).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        q = _filled_qty(r["place_json"])
        if not q:
            continue
        sign = -1.0 if r["side"] == "sell" else 1.0
        out[r["symbol"]] = out.get(r["symbol"], 0.0) + sign * q
    return out


def club_shares_derived(member_id: int, *, client=None,
                        account_number: str | None = None) -> dict[str, float]:
    """Feed-derived club position (>1e-9 only), keys ∩ club-ever-traded symbols.
    Drop-in for club_shares' shape. Raises DerivationIncomplete on an incomplete/failed
    feed read — the caller fails closed. Returns {} ONLY for a genuinely account-less
    member (a real empty, never a read failure)."""
    if account_number is None:
        account_number = vault.account_number_for(member_id)
    if not account_number:
        return {}
    if client is None:
        client = vault.ensure_client(member_id)
    orders = _fetch_full_order_feed(client, account_number)   # raises DerivationIncomplete
    net = club_position_from_orders(orders)
    # Fold renamed tickers FORWARD before the ledger gate, then add the shares a
    # corporate action created (which have no order and so cannot be in the
    # feed). Without both, a renamed holding is invisible here, never sold, and
    # fails verification as "dropped from basket but still held" every cycle.
    renames = symbol_renames()
    if renames:
        folded: dict[str, float] = {}
        for sym, q in net.items():
            folded[renames.get(sym, sym)] = folded.get(renames.get(sym, sym), 0.0) + q
        net = folded
    for sym, q in corporate_action_shares(member_id).items():
        net[sym] = net.get(sym, 0.0) + q
    club_syms = _club_traded_symbols(member_id)
    out: dict[str, float] = {}
    for s, q in net.items():
        if s not in club_syms:                      # broker mis-tag guard (ledger-gated key set)
            continue
        if q > 1e-9:
            out[s] = q
        elif q < -1e-6:
            audit("system", "derived_negative_net", str(member_id), {"symbol": s, "net": round(q, 8)})
    return out


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


def live_positions_strict(member_id: int) -> list[dict]:
    """Long positions [{symbol, quantity}], but RAISE on a read failure instead of
    returning [] — so a caller can tell 'the account is empty' from 'the read failed'.
    Buy-sizing MUST use this: a silent [] on a throttle/timeout would size every basket
    name as held-nothing and re-buy the full basket. Retries once. No connected
    account → [] (a genuine, non-failed empty)."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return []
    last = None
    for attempt in range(2):
        try:
            client = vault.ensure_client(member_id)
            pos = (client.get_positions(acct).get("data") or {}).get("positions") or []
            out = []
            for p in pos:
                q = _num(p.get("quantity")) or 0.0
                if p.get("type") == "long" and q > 1e-9 and p.get("symbol"):
                    out.append({"symbol": p["symbol"], "quantity": q})
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 0:
                time.sleep(1.0)
    raise RuntimeError(f"live_positions read failed for member {member_id}: {last}")


def live_positions(member_id: int) -> list[dict]:
    """Best-effort long positions — [] on ANY read failure (lenient). Use this only
    where an empty result on failure is safe (dust cleanup, liquidation). Buy-sizing
    must NOT use this — see live_positions_strict."""
    try:
        return live_positions_strict(member_id)
    except Exception:  # noqa: BLE001
        return []
