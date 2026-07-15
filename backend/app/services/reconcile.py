"""Fill reconciliation — make the club's ledger match the broker's real fills.

WHY THIS EXISTS (the "not all shares get sold" bug):
A dollar-denominated market BUY and a share market SELL both return
synchronously with cumulative_quantity=0, so the executor stamps an ESTIMATE
(`_concord_est_qty` = dollars/quote for buys, the requested share count for
sells). `positions.club_shares` falls back to that estimate when no real fill is
recorded — but the estimate drifts from the actual fill (filled at a different
price than the quote, partial fills, rounding). Over ~10 cycles the drift
accumulates two ways:
  • full exits sized off the estimate leave a sub-cent CRUMB in the account
    (the shares that "didn't fully sell"), and
  • the ledger keeps PHANTOM shares of names that were actually fully sold.

The fix is to reconcile to truth: read each placed order's real fill from the
broker — matched by our deterministic uuid5 ref_id — and rewrite place_json so
`club_shares` (which already prefers a real cumulative_quantity over the
estimate) becomes fill-based. Then the next full exit sells the true quantity
and the residual collapses to genuine dust the A7 sweep clears.

SAFETY: this PLACES NO ORDERS. It only reads the broker's order list and writes
the club DB. It also promotes an order that ERRORED on our side but actually
FILLED at the broker to status='placed' (closing the errored-SELL gap that A4
only covered for buys), and demotes a 'placed' row the broker actually
REJECTED/CANCELLED to 'skipped' (so club_shares stops counting a fill that never
happened).
"""
from __future__ import annotations

import json

from ..db import db
from ..robinhood import vault
from ..robinhood.client import RHError

# Broker order states that mean a real, trustworthy fill quantity is available.
_FILLED_STATES = {"filled", "partially_filled"}
# States that mean the order positively did NOT put shares in the account.
_DEAD_STATES = {"cancelled", "canceled", "rejected", "failed", "voided"}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_orders(client, account_number: str) -> list[dict]:
    """The member's recent equity orders as a flat list (defensive on shape)."""
    payload = client.get_equity_orders(account_number)
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        orders = data.get("orders") or data.get("results") or []
    elif isinstance(data, list):
        orders = data
    else:
        orders = []
    return [o for o in orders if isinstance(o, dict)]


def _index_by_ref(orders: list[dict]) -> dict[str, dict]:
    """ref_id / client_order_id → order. Prefer a filled order if a ref repeats."""
    by_ref: dict[str, dict] = {}
    for o in orders:
        ref = o.get("ref_id") or o.get("client_order_id")
        if not ref:
            continue
        ref = str(ref)
        prev = by_ref.get(ref)
        if prev is None:
            by_ref[ref] = o
            continue
        # keep whichever looks more "done"
        if str(o.get("state") or "").lower() in _FILLED_STATES:
            by_ref[ref] = o
    return by_ref


def _order_state(o: dict) -> str:
    return str(o.get("state") or o.get("status") or "").lower()


def _order_filled_qty(o: dict) -> float | None:
    """Real filled share quantity for an order, if the broker reports one."""
    q = _num(o.get("cumulative_quantity"))
    if q is not None:
        return q
    total = 0.0
    seen = False
    for e in o.get("executions") or []:
        eq = _num(e.get("quantity"))
        if eq is not None:
            total += eq
            seen = True
    return total if seen else None


def reconcile_fills(member_id: int, client=None, account_number: str | None = None,
                    session_id: int | None = None, dry_run: bool = False) -> dict:
    """Reconcile this member's placed/errored executions against real broker fills.

    Matches by our deterministic ref_id only (never a fuzzy symbol/amount match —
    that could claim a member's PERSONAL order). Returns a summary of the changes
    (or would-be changes when dry_run=True). Places no orders.
    """
    out: dict = {"ok": True, "member_id": member_id, "dry_run": dry_run,
                 "reconciled": [], "promoted": [], "demoted": [], "unmatched": 0, "pending": 0}
    if account_number is None:
        account_number = vault.account_number_for(member_id)
    if not account_number:
        return {"ok": False, "member_id": member_id, "error": "no connected account", "reconciled": []}

    # Candidate rows: placed rows still on an estimate (never reconciled) + error
    # rows that may actually have filled. Scoped to a session when asked.
    where = "member_id=? AND status IN ('placed','error')"
    args: list = [member_id]
    if session_id is not None:
        where += " AND session_id=?"
        args.append(session_id)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, session_id, symbol, side, status, ref_id, place_json, dollar_amount "
            f"FROM executions WHERE {where}", args
        ).fetchall()]
    if not rows:
        return out

    try:
        if client is None:
            client = vault.ensure_client(member_id)
        orders = _fetch_orders(client, account_number)
    except RHError as e:
        return {"ok": False, "member_id": member_id, "error": f"broker read failed: {type(e).__name__}: {e}",
                "reconciled": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "member_id": member_id, "error": f"broker read failed: {type(e).__name__}: {e}",
                "reconciled": []}
    by_ref = _index_by_ref(orders)

    updates: list[tuple] = []          # (place_json, status, id) to write
    for r in rows:
        ref = r.get("ref_id")
        order = by_ref.get(str(ref)) if ref else None
        if order is None:
            # No ref match → do NOT guess. An estimate-based placed row simply
            # keeps its estimate; an error row stays for A4 / the next cycle.
            out["unmatched"] += 1
            continue
        state = _order_state(order)
        try:
            pj = json.loads(r["place_json"]) if r["place_json"] else {}
        except Exception:  # noqa: BLE001
            pj = {}
        if not isinstance(pj, dict):
            pj = {}

        if state in _FILLED_STATES:
            real = _order_filled_qty(order)
            if real is None or real <= 0:
                out["pending"] += 1
                continue
            data = pj.setdefault("data", {})
            odict = data.setdefault("order", {})
            prev = _num(odict.get("cumulative_quantity")) or 0.0
            odict["cumulative_quantity"] = real
            odict["state"] = state
            pj["_concord_reconciled"] = True
            new_status = "placed" if r["status"] == "error" else r["status"]
            updates.append((json.dumps(pj), new_status, r["id"]))
            entry = {"symbol": r["symbol"], "side": r["side"], "est_or_prev": round(prev, 8),
                     "real": round(real, 8), "session_id": r["session_id"]}
            if r["status"] == "error":
                out["promoted"].append(entry)
            else:
                out["reconciled"].append(entry)
        elif state in _DEAD_STATES:
            # Positively did not fill. A 'placed' row here was never a real fill →
            # demote so club_shares stops counting it. An 'error' row is correct
            # as-is (buys: A4 re-places; sells: next cycle re-attempts).
            if r["status"] == "placed":
                data = pj.setdefault("data", {})
                odict = data.setdefault("order", {})
                odict["cumulative_quantity"] = 0.0
                odict["state"] = state
                pj["_concord_reconciled"] = True
                updates.append((json.dumps(pj), "skipped", r["id"]))
                out["demoted"].append({"symbol": r["symbol"], "side": r["side"],
                                       "state": state, "session_id": r["session_id"]})
        else:
            out["pending"] += 1  # queued/confirmed/new — still working, try later

    if updates and not dry_run:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for pj_str, status, rid in updates:
                    conn.execute(
                        "UPDATE executions SET place_json=?, status=? WHERE id=?",
                        (pj_str, status, rid),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    out["changed"] = len(updates)
    return out


def reconcile_all(dry_run: bool = False) -> dict:
    """Reconcile every connected agentic member (used by the standalone job)."""
    with db() as conn:
        members = [dict(r) for r in conn.execute(
            "SELECT m.id AS member_id, a.account_number FROM members m "
            "JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE m.is_bot=0 AND a.status='connected' AND a.agentic_allowed=1 ORDER BY m.id"
        ).fetchall()]
    results = []
    for m in members:
        results.append(reconcile_fills(m["member_id"], account_number=m["account_number"], dry_run=dry_run))
    return {"ok": True, "dry_run": dry_run, "members": results}
