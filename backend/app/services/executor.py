"""Per-member executor — builds and (optionally) places the consensus basket.

Sizing: each opted-in member buys every consensus pick with an equal
dollar slice of their budget, where
    budget   = min(buying_power × bp_pct%, daily_cap_usd, MAX_NOTIONAL_PER_MEMBER)
    per_pick = budget / N
Orders are dollar-denominated MARKET buys in regular hours (the only form that
supports a dollar amount + fractional shares).

Safety (see ARCHITECTURE.md §6):
- A real `place_equity_order` happens ONLY when execution_mode=live AND the
  master ARMED flag is on AND dry_run is not forced.
- `review_equity_order` ALWAYS runs first; blocking alerts → the order is skipped.
- ref_id is a deterministic uuid5 per (session,member,symbol) → idempotent.
- Already-`placed` orders are never re-placed or overwritten.
"""
from __future__ import annotations

import json
import math
import time
import uuid

from ..config import settings
from ..db import audit, db, is_armed
from ..robinhood import vault
from . import alloc, positions, voting

_REF_NS = uuid.UUID("c0c0c0c0-0000-4000-8000-000000000000")


def ref_id_for(session_id: int, member_id: int, symbol: str) -> str:
    return str(uuid.uuid5(_REF_NS, f"{session_id}:{member_id}:{symbol}"))


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def deployable(buying_power: float) -> float:
    """All available buying power, less a small cash reserve so dollar market
    orders don't trip a buying-power reject on the last leg."""
    return buying_power * (1.0 - settings.cash_buffer_pct / 100.0)


# EQUITY_SUITABILITY is a disclosure that shows on every order for individual
# agentic accounts. It's benign for an ONBOARDED account (orders place + fill
# fine — confirmed live). For an account that hasn't finished Robinhood's
# investing-goals questionnaire the place still fails at 400, but that surfaces
# as a normal order error (recorded, no money moves) — so we let suitability
# through and let the broker be the final gate. Real blockers (buying power,
# PDT, halts) are NOT in this set and still skip the order pre-trade.
_BENIGN_ALERTS = {"EQUITY_SUITABILITY"}


def has_blocking_alerts(review: dict) -> tuple[bool, list]:
    """Authoritative pre-trade gate, verified against the live review payload.

    Robinhood's review_equity_order returns broker alerts under `order_checks`
    (an empty {} means NO alerts — confirmed live). For an UNATTENDED real-money
    executor we block on any alert EXCEPT a known-benign allowlist
    (`_BENIGN_ALERTS`). Returns (blocked, blocking_items).
    """
    data = review.get("data", review) if isinstance(review, dict) else {}
    if not isinstance(data, dict):
        return False, []
    raw: list = []
    checks = data.get("order_checks")
    if isinstance(checks, dict) and checks:
        raw.append(checks)
    elif isinstance(checks, list):
        raw += [c for c in checks if c]
    for legacy in ("alerts", "blocking_alerts", "warnings"):
        v = data.get(legacy)
        if isinstance(v, list):
            raw += [x for x in v if x]
        elif v:
            raw.append(v)
    blocking = [a for a in raw
                if not (isinstance(a, dict) and a.get("alertType") in _BENIGN_ALERTS)]
    return (len(blocking) > 0), blocking


def _opted_members() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id AS member_id, m.display_name, s.alloc_type, s.alloc_value,"
            "       s.max_orders, a.account_number, a.agentic_allowed, a.status AS rh_status "
            "FROM members m "
            "JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE s.opted_in=1"
        ).fetchall()
    return [dict(r) for r in rows]


def _record(session_id, member_id, symbol, dollar_amount, ref_id, status,
            review=None, place=None, error=None, side="buy") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,review_json,place_json,error,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, member_id, symbol, side, "market", dollar_amount, ref_id, status,
             json.dumps(review) if review is not None else None,
             json.dumps(place) if place is not None else None,
             error, int(time.time())),
        )


def _placed_keys(session_id: int) -> set[tuple[int, str, str]]:
    """(member, symbol, side) already placed this session — never re-place."""
    with db() as conn:
        rows = conn.execute(
            "SELECT member_id,symbol,side FROM executions WHERE session_id=? AND status='placed'",
            (session_id,),
        ).fetchall()
    return {(r["member_id"], r["symbol"], r["side"]) for r in rows}


def _quotes(client, symbols: list[str]) -> dict[str, float]:
    """last_trade_price per symbol via the member's own RH client (best-effort)."""
    out: dict[str, float] = {}
    syms = sorted({s for s in symbols if s})
    for i in range(0, len(syms), 20):
        try:
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
        except Exception:  # noqa: BLE001
            continue
        for e in res:
            q = e.get("quote") or {}
            px = _num(q.get("last_trade_price"))
            if q.get("symbol") and px:
                out[q["symbol"]] = px
    return out


def _clear_replanable(session_id: int) -> None:
    """Drop prior non-final rows so a re-run is clean; never touch 'placed'."""
    with db() as conn:
        conn.execute(
            "DELETE FROM executions WHERE session_id=? AND status IN "
            "('planned','reviewed','skipped','error')",
            (session_id,),
        )


def run(session: dict, force_dry_run: bool = False) -> dict:
    """Execute the closed session's basket. Returns a structured summary."""
    if session["status"] == "open":
        raise ValueError("session is still open — close/tally first")
    picks = [p["symbol"] for p in voting.session_picks(session)]
    if not picks:
        return {"ok": True, "note": "no consensus picks — nothing to execute", "orders": []}

    armed = is_armed()
    will_place = settings.is_live and armed and not force_dry_run
    mode = "LIVE" if will_place else ("DRY_RUN(disarmed)" if settings.is_live else "DRY_RUN")
    sid = session["id"]

    _clear_replanable(sid)
    already = _placed_keys(sid)
    summary = {"mode": mode, "trade_date": session["trade_date"], "picks": picks,
               "will_place": will_place, "members": [], "orders": []}
    audit("system", "execute_start", session["trade_date"],
          {"mode": mode, "picks": picks, "will_place": will_place})

    from ..db import kv_get
    for m in _opted_members():
        if kv_get("halt") == "1":
            summary["halted"] = True
            audit("system", "execute_halted", session["trade_date"], None)
            break
        mid = m["member_id"]
        msum = {"member_id": mid, "name": m["display_name"], "orders": [], "note": None}
        if not m.get("account_number") or not m.get("agentic_allowed") or m.get("rh_status") != "connected":
            msum["note"] = "no connected agentic Robinhood account"
            summary["members"].append(msum)
            continue
        try:
            client = vault.ensure_client(mid)
            port = client.get_portfolio(m["account_number"])
            pdata = port.get("data", port) if isinstance(port, dict) else {}
            bp = pdata.get("buying_power") or (pdata.get("buying_power") or {})
            buying_power = _num(bp.get("buying_power") if isinstance(bp, dict) else bp) or 0.0
            total_value = (_num(pdata.get("total_value")) or _num(pdata.get("equity_value"))
                           or buying_power)
        except Exception as e:  # noqa: BLE001
            msum["note"] = f"portfolio fetch failed: {type(e).__name__}: {e}"
            vault.mark_status(mid, "error")
            summary["members"].append(msum)
            continue

        # Rebalance target = committed stake (frees cash via sells), NOT buying power.
        budget = alloc.rebalance_budget(m["alloc_type"], m["alloc_value"], total_value, buying_power)
        # Broker minimum is $1/order, so we can hold at most floor(budget) names at
        # equal weight. With a small stake the member holds the TOP names only.
        n_eff = max(1, min(len(picks), int(budget // 1.00)))
        basket = picks[:n_eff]
        per_pick = math.floor((budget / n_eff) * 100) / 100.0
        want = set(basket)
        held = positions.club_shares(mid)            # only what the CLUB bought
        prices = _quotes(client, list(want | set(held)))
        msum["buying_power"] = round(buying_power, 2)
        msum["allocation"] = round(budget, 2)
        msum["budget"] = round(budget, 2)
        msum["per_pick"] = per_pick
        order_count = 0

        # ── SELL phase: exit dropped names + trim overweight basket names ──
        # Sells come ONLY from `held` (club-bought, reconstructed from executions),
        # so a member's own non-club positions are never touched. A name that left
        # the basket is fully sold; a name still in the basket but OVER its equal-
        # weight target is trimmed back to target, so the proceeds redistribute
        # into the rest of the new basket.
        for symbol, qty in sorted(held.items()):
            price = prices.get(symbol)
            if symbol in want:
                if price is None:
                    continue                          # can't size a trim without a price
                excess = qty - (per_pick / price)     # shares above the equal-weight target
                if excess * price < 1.00:             # not meaningfully overweight → keep
                    continue
                sell_qty, why = excess, "trim to target"
            else:
                sell_qty, why = qty, "exited basket"  # full exit of a dropped name
            if (mid, symbol, "sell") in already:
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "placed",
                                       "note": "already sold"})
                continue
            ref_id = ref_id_for(sid, mid, f"SELL:{symbol}")
            qfloor = math.floor(sell_qty * 1e6) / 1e6   # never round UP past what we hold
            if qfloor <= 0:
                continue
            qstr = f"{qfloor:.6f}"
            try:
                review = client.review_market_sell(m["account_number"], symbol, qstr)
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, None, ref_id, "error", error=f"sell review: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "error", "note": str(e)})
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                _record(sid, mid, symbol, None, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped", "alerts": alerts})
                continue
            if not will_place:
                _record(sid, mid, symbol, None, ref_id, "reviewed", review=review, side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "reviewed", "qty": qstr, "note": why})
                continue
            try:
                place = client.place_market_sell(m["account_number"], symbol, qstr, ref_id=ref_id)
                if isinstance(place, dict):
                    place["_concord_est_qty"] = qfloor   # shares sold (broker fill is async)
                _record(sid, mid, symbol, None, ref_id, "placed", review=review, place=place, side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "placed", "qty": qstr, "note": why})
                order_count += 1
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, None, ref_id, "error", review=review, error=f"sell: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "error", "note": str(e)})

        # ── BUY phase: top each basket name up to its equal-weight target ──
        # need = target − current club value, so names already held aren't re-bought
        # and the proceeds from the sells above fund the newly-added names.
        for symbol in basket:
            if (mid, symbol, "buy") in already:
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed",
                                       "note": "already placed"})
                continue
            price = prices.get(symbol)
            if price is None:
                # can't value an existing holding or estimate filled shares → skip,
                # so we never blindly re-buy a name we may already hold.
                _record(sid, mid, symbol, None, ref_id_for(sid, mid, symbol), "skipped",
                        error="no quote for sizing")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": "no quote"})
                continue
            held_val = held.get(symbol, 0.0) * price
            need = round(per_pick - held_val, 2)
            if need < 1.00:
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "held",
                                       "note": f"at target (${held_val:.2f} ≥ ${per_pick:.2f})"})
                continue
            if order_count >= settings.max_orders:
                _record(sid, mid, symbol, need, ref_id_for(sid, mid, symbol), "skipped",
                        error="max_orders cap reached")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": "max_orders cap"})
                continue
            ref_id = ref_id_for(sid, mid, symbol)
            amt = f"{need:.2f}"
            try:
                review = client.review_market_buy(m["account_number"], symbol, amt)
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, need, ref_id, "error", error=f"review: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "error", "note": str(e)})
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                _record(sid, mid, symbol, need, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "alerts": alerts})
                continue
            if not will_place:
                _record(sid, mid, symbol, need, ref_id, "reviewed", review=review)
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "reviewed", "amount": amt})
                continue
            try:
                place = client.place_market_buy(m["account_number"], symbol, amt, ref_id=ref_id)
                if isinstance(place, dict):
                    place["_concord_est_qty"] = round(need / price, 6)  # est shares (async fill)
                _record(sid, mid, symbol, need, ref_id, "placed", review=review, place=place)
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed", "amount": amt})
                order_count += 1
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, need, ref_id, "error", review=review, error=f"place: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "error", "note": str(e)})
        summary["members"].append(msum)
        summary["orders"].extend(
            {"member_id": mid, **o} for o in msum["orders"]
        )

    if will_place:
        with db() as conn:
            conn.execute("UPDATE vote_sessions SET status='executed' WHERE id=?", (sid,))
    # Cumulative truth for the session — a re-run places 0 NEW orders even though
    # the first run may have placed many, so report the session total, not just
    # this run, and a per-reason skip breakdown so the UI never under-reports.
    this_run = summary["orders"]
    skips: dict[str, int] = {}
    for o in this_run:
        if o.get("status") == "skipped":
            reason = (o.get("alerts") or [{}])[0].get("alertType") if o.get("alerts") else o.get("note")
            skips[reason or "skipped"] = skips.get(reason or "skipped", 0) + 1
    with db() as conn:
        summary["placed_total"] = conn.execute(
            "SELECT COUNT(*) FROM executions WHERE session_id=? AND status='placed'", (sid,)
        ).fetchone()[0]
    summary["skipped"] = skips
    audit("system", "execute_done", session["trade_date"],
          {"mode": mode, "n_members": len(summary["members"])})
    return summary
