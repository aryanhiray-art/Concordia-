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

import datetime
import json
import time
from urllib.parse import parse_qs, urlparse

from ..db import audit, db
from ..robinhood import vault
from . import membership, positions
from ..robinhood.client import RHError

# Broker order states that mean a real, trustworthy fill quantity is available.
_FILLED_STATES = {"filled", "partially_filled"}
# States that mean the order positively did NOT put shares in the account.
_DEAD_STATES = {"cancelled", "canceled", "rejected", "failed", "voided"}

# The Robinhood agentic API returns ref_id=null on every order, so we CANNOT
# match on our ref_id. Instead we match a club execution to a broker order by
# symbol + side + closest created-time, but ONLY among orders this agent placed
# (placed_agent='agentic') — a member's personal ('user') order is never a
# candidate, so reconciliation can never touch personal shares. (symbol,side,day)
# is unique among agentic orders, and cycles are ≥1 day apart, so the window
# below (well under the inter-cycle gap) yields an unambiguous 1:1 match.
_MATCH_TOLERANCE_S = 20 * 3600      # ±20h
_MAX_ORDER_PAGES = 12               # paginate deep enough to cover the club's history


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(v) -> float | None:
    """Epoch seconds from an order's created time (epoch number or ISO string)."""
    if v is None:
        return None
    n = _num(v)
    if n is not None:
        return n
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cursor_from_next(nxt) -> str | None:
    if not nxt:
        return None
    try:
        c = parse_qs(urlparse(str(nxt)).query).get("cursor")
        return c[0] if c else None
    except Exception:  # noqa: BLE001
        return None


def _fetch_agentic_orders(client, account_number: str) -> list[dict]:
    """All recent AGENTIC equity orders (club orders only — never personal),
    following pagination. The placed_agent='agentic' filter is the club/personal
    safety boundary; we also drop any explicitly non-agentic row defensively."""
    out: list[dict] = []
    cursor: str | None = None
    for _ in range(_MAX_ORDER_PAGES):
        payload = client.get_equity_orders(account_number, cursor=cursor, placed_agent="agentic")
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            orders = data.get("orders") or data.get("results") or []
            nxt = data.get("next")
        elif isinstance(data, list):
            orders, nxt = data, None
        else:
            orders, nxt = [], None
        out += [o for o in orders
                if isinstance(o, dict) and o.get("placed_agent") in (None, "agentic")]
        cursor = _cursor_from_next(nxt)
        if not cursor or not orders:
            break
    return out


def _index_orders(orders: list[dict]) -> dict[tuple[str, str], list[tuple[float, dict]]]:
    """(SYMBOL, side) → [(created_ts, order), …] for closest-time matching."""
    idx: dict[tuple[str, str], list[tuple[float, dict]]] = {}
    for o in orders:
        sym = str(o.get("symbol") or "").upper()
        side = str(o.get("side") or "").lower()
        ts = _parse_ts(o.get("created_at") or o.get("created_time") or o.get("updated_at"))
        if not sym or not side or ts is None:
            continue
        idx.setdefault((sym, side), []).append((ts, o))
    return idx


def _match_order(idx, symbol: str, side: str, exec_ts: float | None) -> dict | None:
    """The agentic order best matching this execution: same symbol+side, created
    closest in time, within the tolerance window. None if nothing close enough."""
    cands = idx.get((str(symbol).upper(), str(side).lower()))
    if not cands or exec_ts is None:
        return None
    best, best_dt = None, None
    for ots, o in cands:
        dt = abs(ots - exec_ts)
        if best_dt is None or dt < best_dt:
            best, best_dt = o, dt
    if best is not None and best_dt is not None and best_dt <= _MATCH_TOLERANCE_S:
        return best
    return None


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
            f"SELECT id, session_id, symbol, side, status, ref_id, place_json, dollar_amount, created_at "
            f"FROM executions WHERE {where}", args
        ).fetchall()]
    if not rows:
        return out

    try:
        if client is None:
            client = vault.ensure_client(member_id)
        orders = _fetch_agentic_orders(client, account_number)
    except RHError as e:
        return {"ok": False, "member_id": member_id, "error": f"broker read failed: {type(e).__name__}: {e}",
                "reconciled": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "member_id": member_id, "error": f"broker read failed: {type(e).__name__}: {e}",
                "reconciled": []}
    idx = _index_orders(orders)
    out["agentic_orders"] = len(orders)

    updates: list[tuple] = []          # (place_json, status, id) to write
    for r in rows:
        order = _match_order(idx, r["symbol"], r["side"], _num(r.get("created_at")))
        if order is None:
            # No close agentic order → do NOT guess. An estimate-based placed row
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




def ledger_trueup(member_id: int, *, client=None, account_number: str | None = None,
                  dry_run: bool = True) -> dict:
    """Zero PHANTOM club-ledger rows — names the derived ledger says the member
    holds while the live account holds none of the symbol at all.

    Why they exist: the ledger is net-of-order-feed, and old sells recorded with
    estimated quantities can never be trued up once the broker's feed pages past
    them — so residuals from July sit there forever. They place no trades (the
    sell leg caps at the live quantity), but every cycle's verification flags
    them ("ledger lists N names the account no longer holds") and the WARN noise
    buries real findings. Member 2 carried 19 of these.

    Mechanism copied from renames.migrate, the proven corporate-action true-up:
    an APPEND-ONLY $0 execution row per symbol carrying the corrective quantity
    in _concord_est_qty — history is never edited, and the derived net folds to
    zero. Guards: live position must be exactly absent (never "small"), and any
    symbol with an execution row in the last 48h is skipped so an in-flight exit
    whose fill has not reached the feed yet is left alone. ref_id carries the
    date, so a rerun today is idempotent while a future re-drift can be trued
    again.
    """
    if account_number is None:
        account_number = vault.account_number_for(member_id)
    if not account_number:
        return {"ok": False, "member_id": member_id, "error": "no account"}
    if client is None:
        client = vault.ensure_client(member_id)

    derived = positions.club_shares_derived(member_id, client=client,
                                            account_number=account_number)
    # STRICT on purpose: the lenient live_positions returns [] on a failed read,
    # which would make every derived row look phantom and zero the whole ledger.
    # A raise here is caught per-member by reconcile_all and skips the sweep.
    live = {p["symbol"].upper(): float(p.get("quantity") or 0.0)
            for p in positions.live_positions_strict(member_id)}

    now = int(time.time())
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    cutoff = now - 48 * 3600
    zeroed: list[dict] = []
    with db() as conn:
        sid = conn.execute("SELECT COALESCE(MAX(id),0) FROM vote_sessions").fetchone()[0]
        for sym, qty in sorted(derived.items()):
            if qty <= 1e-9:
                continue
            if live.get(sym, 0.0) > 0:
                continue                       # genuinely held → not a phantom
            recent = conn.execute(
                "SELECT 1 FROM executions WHERE member_id=? AND symbol=? AND created_at>=? LIMIT 1",
                (member_id, sym, cutoff)).fetchone()
            if recent:
                continue                       # in-flight exit — leave for the feed
            ref = f"TRUEUP:{member_id}:{sym}:{day}"
            if conn.execute("SELECT 1 FROM executions WHERE ref_id=?", (ref,)).fetchone():
                continue
            zeroed.append({"symbol": sym, "qty": round(qty, 8)})
            if dry_run:
                continue
            conn.execute(
                "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
                "ref_id,status,place_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (sid, member_id, sym, "sell", "market", 0.0, ref, "placed",
                 json.dumps({"_concord_est_qty": qty,
                             "ledger_trueup": "phantom — account holds none"}),
                 None, now))
    if zeroed and not dry_run:
        audit("system:reconcile", "ledger_trueup", str(member_id),
              {"zeroed": zeroed[:30], "count": len(zeroed)})
    return {"ok": True, "member_id": member_id, "dry_run": dry_run, "zeroed": zeroed}


def reconcile_all(dry_run: bool = False) -> dict:
    """Reconcile every connected agentic member (used by the standalone job)."""
    with db() as conn:
        members = [dict(r) for r in conn.execute(
            "SELECT m.id AS member_id, a.account_number FROM members m "
            "JOIN rh_accounts a ON a.member_id=m.id "
            f"WHERE m.is_bot=0 AND a.status='connected' AND a.agentic_allowed=1 "
            f"AND {membership.active_clause('m')} ORDER BY m.id"
        ).fetchall()]
    results = []
    for m in members:
        res = reconcile_fills(m["member_id"], account_number=m["account_number"], dry_run=dry_run)
        # After fills are trued up, zero any phantom rows the feed can no longer
        # explain — one member's crash must not stop the sweep for the rest.
        try:
            res["trueup"] = ledger_trueup(m["member_id"],
                                          account_number=m["account_number"],
                                          dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            res["trueup"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        results.append(res)
    return {"ok": True, "dry_run": dry_run, "members": results}
