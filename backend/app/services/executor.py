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
from . import voting

_REF_NS = uuid.UUID("c0c0c0c0-0000-4000-8000-000000000000")


def ref_id_for(session_id: int, member_id: int, symbol: str) -> str:
    return str(uuid.uuid5(_REF_NS, f"{session_id}:{member_id}:{symbol}"))


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def budget_for(buying_power: float, bp_pct: float, daily_cap: float) -> float:
    return min(buying_power * (bp_pct / 100.0), daily_cap, settings.max_notional_per_member)


def has_blocking_alerts(review: dict) -> tuple[bool, list]:
    """Authoritative pre-trade gate, verified against the live review payload.

    Robinhood's review_equity_order returns broker alerts under `order_checks`
    (an empty {} means NO alerts — confirmed live). For an UNATTENDED real-money
    executor we take the safe stance: any non-empty order_checks (or legacy
    alert/warning field) blocks the order; the owner can inspect review_json and
    relax later. Returns (blocked, items).
    """
    data = review.get("data", review) if isinstance(review, dict) else {}
    if not isinstance(data, dict):
        return False, []
    items: list = []
    checks = data.get("order_checks")
    if isinstance(checks, dict):
        items += [{k: v} for k, v in checks.items()]
    elif isinstance(checks, list):
        items += checks
    for legacy in ("alerts", "blocking_alerts", "warnings"):
        v = data.get(legacy)
        if isinstance(v, list):
            items += v
        elif v:
            items.append(v)
    return (len(items) > 0), items


def _opted_members() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id AS member_id, m.display_name, s.buying_power_pct, s.daily_cap_usd,"
            "       s.max_orders, a.account_number, a.agentic_allowed, a.status AS rh_status "
            "FROM members m "
            "JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE s.opted_in=1"
        ).fetchall()
    return [dict(r) for r in rows]


def _record(session_id, member_id, symbol, dollar_amount, ref_id, status,
            review=None, place=None, error=None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,review_json,place_json,error,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, member_id, symbol, "buy", "market", dollar_amount, ref_id, status,
             json.dumps(review) if review is not None else None,
             json.dumps(place) if place is not None else None,
             error, int(time.time())),
        )


def _placed_keys(session_id: int) -> set[tuple[int, str]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT member_id,symbol FROM executions WHERE session_id=? AND status='placed'",
            (session_id,),
        ).fetchall()
    return {(r["member_id"], r["symbol"]) for r in rows}


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
        except Exception as e:  # noqa: BLE001
            msum["note"] = f"portfolio fetch failed: {type(e).__name__}: {e}"
            vault.mark_status(mid, "error")
            summary["members"].append(msum)
            continue

        budget = budget_for(buying_power, m["buying_power_pct"], m["daily_cap_usd"])
        per_pick = math.floor((budget / len(picks)) * 100) / 100.0
        msum["buying_power"] = round(buying_power, 2)
        msum["budget"] = round(budget, 2)
        msum["per_pick"] = per_pick
        if per_pick < 1.00:
            msum["note"] = f"per-pick ${per_pick:.2f} below $1 minimum — skipped"
            summary["members"].append(msum)
            continue

        placed_count = 0
        for symbol in picks:
            if (mid, symbol) in already:
                msum["orders"].append({"symbol": symbol, "status": "placed", "note": "already placed"})
                continue
            if placed_count >= m["max_orders"]:
                _record(sid, mid, symbol, per_pick, ref_id_for(sid, mid, symbol), "skipped",
                        error="max_orders cap reached")
                msum["orders"].append({"symbol": symbol, "status": "skipped", "note": "max_orders cap"})
                continue
            ref_id = ref_id_for(sid, mid, symbol)
            amt = f"{per_pick:.2f}"
            try:
                review = client.review_market_buy(m["account_number"], symbol, amt)
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, per_pick, ref_id, "error", error=f"review: {e}")
                msum["orders"].append({"symbol": symbol, "status": "error", "note": str(e)})
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                _record(sid, mid, symbol, per_pick, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert")
                msum["orders"].append({"symbol": symbol, "status": "skipped", "alerts": alerts})
                continue
            if not will_place:
                _record(sid, mid, symbol, per_pick, ref_id, "reviewed", review=review)
                msum["orders"].append({"symbol": symbol, "status": "reviewed", "amount": amt})
                continue
            try:
                place = client.place_market_buy(m["account_number"], symbol, amt, ref_id=ref_id)
                _record(sid, mid, symbol, per_pick, ref_id, "placed", review=review, place=place)
                msum["orders"].append({"symbol": symbol, "status": "placed", "amount": amt})
                placed_count += 1
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, per_pick, ref_id, "error", review=review, error=f"place: {e}")
                msum["orders"].append({"symbol": symbol, "status": "error", "note": str(e)})
        summary["members"].append(msum)
        summary["orders"].extend(
            {"member_id": mid, **o} for o in msum["orders"]
        )

    if will_place:
        with db() as conn:
            conn.execute("UPDATE vote_sessions SET status='executed' WHERE id=?", (sid,))
    audit("system", "execute_done", session["trade_date"],
          {"mode": mode, "n_members": len(summary["members"])})
    return summary
