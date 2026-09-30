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
broker — matched by the broker order id a row already carries, else by symbol +
side + same-ET-date closest-time among agentic orders — and rewrite place_json so
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
import uuid

from ..calendar_us import ET, today_et
from ..db import audit, db
from ..robinhood import vault
from . import membership, positions
from ..robinhood.client import RHError

# Broker order states that mean a real, trustworthy fill quantity is available.
_FILLED_STATES = {"filled", "partially_filled"}
# States that mean the order positively did NOT put shares in the account.
_DEAD_STATES = {"cancelled", "canceled", "rejected", "failed", "voided"}

# The Robinhood agentic API returns ref_id=null on every order, so we CANNOT
# match on our ref_id. A row that already carries a broker order id (stamped by
# a prior pass, or embedded in the synchronous place response) binds to exactly
# that order. Everything else matches by symbol + side + SAME US/Eastern
# calendar date + closest created-time, but ONLY among orders this agent placed
# (placed_agent='agentic') — a member's personal ('user') order is never a
# candidate, so reconciliation can never touch personal shares. The cadence is
# daily and a row is stamped seconds from its broker order, so the ET-date bind
# plus a tight window removes cross-session ambiguity (a 20h window bought
# nothing but that ambiguity); each broker order is claimed by at most one row.
_MATCH_TOLERANCE_S = 2 * 3600       # ±2h
_MAX_ORDER_PAGES = 12               # paginate deep enough to cover the club's history

# An unmatched 'error' row older than this is aged out (spans a weekend on the
# daily cadence) — see reconcile_fills' aging pass.
_ERROR_AGE_OUT_S = 3 * 86400


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
    """The broker's `next` is a bare opaque token, not a URL — decoded in one
    place (robinhood.feed) for every feed reader."""
    from ..robinhood.feed import cursor_from_next
    return cursor_from_next(nxt)


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
        if not nxt:
            break                   # the broker said: that is everything
        cursor = _cursor_from_next(nxt)
        if cursor is None:
            break                   # a page we cannot fetch — the oldest-order proof stays honest
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


def _et_date(ts: float) -> datetime.date:
    """US/Eastern calendar date of an epoch timestamp — the fuzzy match binds
    to the trade date, and an epoch's UTC date leaks after ~8pm ET."""
    return datetime.datetime.fromtimestamp(ts, tz=ET).date()


def _order_id(o: dict) -> str:
    return str(o.get("id") or o.get("order_id") or "")


def _order_amount(order: dict) -> float | None:
    """Dollar amount of a broker order. Robinhood carries it ONLY under
    dollar_based_amount.amount — decoded in robinhood.feed for every reader
    (the five-key list this used to search never matched a real order, so
    the economic promotion gate below refused every filled buy)."""
    from ..robinhood.feed import order_amount
    return order_amount(order)


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


def _session_retired(session_id: int) -> bool:
    """True once a session can no longer place anything for itself.

    An errored BUY may only be aged out (and its dollars credited back to the
    slice) when its session is terminal or too stale to re-run — otherwise a
    live re-run of that session would re-place the order AND keep the credit,
    deploying the same dollars twice. 5 days mirrors executor.STALE_BASKET_DAYS,
    which is what actually refuses the buy leg (not imported: executor imports
    this module).
    """
    with db() as conn:
        row = conn.execute(
            "SELECT status, trade_date FROM vote_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return True
    if row["status"] in ("executed", "lapsed"):
        return True
    try:
        y, m, d = (int(x) for x in str(row["trade_date"] or "").split("-"))
    except ValueError:
        return False
    return (today_et() - datetime.date(y, m, d)).days > 5


def _is_repair_row(r: dict) -> bool:
    """True for a crumb-repair (DUSTFIX) buy row.

    Repair dollars are account cash fronted OUTSIDE the member's commitment —
    only a PLACED repair is charged to the slice (executor debits slice_carry
    for it), so an unplaced one must never be credited back into the slice.
    ref_ids are opaque uuid5, so the only way to recognise one is to recompute
    it; the namespace mirrors executor._REF_NS (executor imports this module, so
    the constant is duplicated rather than imported).
    """
    ns = uuid.UUID("c0c0c0c0-0000-4000-8000-000000000000")
    want = str(uuid.uuid5(ns, f"{r['session_id']}:{r['member_id']}:DUSTFIX:{r['symbol']}"))
    return str(r.get("ref_id") or "") == want


def reconcile_fills(member_id: int, client=None, account_number: str | None = None,
                    session_id: int | None = None, dry_run: bool = False) -> dict:
    """Reconcile this member's placed/errored executions against real broker fills.

    Matching is broker-order-id-bound where a row already carries one, otherwise
    fuzzy by symbol + side + same-ET-date + closest created-time — 1:1, so each
    broker order reconciles at most one row ('placed' rows claim before 'error'
    rows), and only among agentic orders (never a member's personal order). An
    'error' row is promoted to 'placed' only when the matched order's size agrees
    with the row's own recorded intent. Unmatched 'error' rows past the age-out
    window are retired; an aged-out BUY credits the member's carry_pending once
    (the dollars never deployed, so they must ride the next cycle — the buy
    leg's write-back drains that column into the slice). Returns a
    summary of the changes (or would-be changes when dry_run=True). Places no
    orders.
    """
    out: dict = {"ok": True, "member_id": member_id, "dry_run": dry_run,
                 "reconciled": [], "promoted": [], "demoted": [], "unmatched": 0,
                 "pending": 0, "unchanged": 0, "aged": [], "carry_credited": []}
    if account_number is None:
        account_number = vault.account_number_for(member_id)
    if not account_number:
        return {"ok": False, "member_id": member_id, "error": "no connected account", "reconciled": []}

    # Candidate rows: placed rows still on an estimate (never reconciled) + error
    # rows that may actually have filled. Scoped to a session when asked.
    # Synthetic corrective rows (TRUEUP / CORPACT) have no broker order behind
    # them and must never be time-matched to a real one.
    # 'skipped' rows are normally finished business, but one carrying a parked
    # carry claim (an order the broker killed whose session could still have
    # re-placed it) must stay visible until that claim is paid or deleted.
    where = ("member_id=? AND (status IN ('placed','error') "
             "OR (status='skipped' AND place_json LIKE '%_concord_awaiting_credit%')) "
             "AND ref_id NOT LIKE 'TRUEUP:%' AND ref_id NOT LIKE 'CORPACT:%'")
    args: list = [member_id]
    if session_id is not None:
        where += " AND session_id=?"
        args.append(session_id)
    with db() as conn:
        fetched = [dict(r) for r in conn.execute(
            f"SELECT id, session_id, member_id, symbol, side, status, ref_id, place_json, "
            f"dollar_amount, error, created_at "
            f"FROM executions WHERE {where}", args
        ).fetchall()]
    rows: list[dict] = []
    for r in fetched:
        raw = r.get("place_json") or ""
        if "ledger_trueup" in raw or "corporate_action" in raw:
            continue                    # corrective row that slipped the ref_id filter
        try:
            pj = json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            pj = {}
        if not isinstance(pj, dict):
            pj = {}
        if pj.get("_concord_aged_out"):
            continue                    # terminally aged — never re-scan or re-age
        r["_pj"] = pj
        rows.append(r)
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

    # 1:1 claims — an order's key is its broker id, or a list-index surrogate
    # for id-less orders. Claim order: id-bound rows first, then 'placed' rows
    # by global-nearest greedy, then 'error' rows over whatever is left — so
    # the row created seconds from an order always beats a stale row hours away.
    by_id: dict[str, dict] = {}
    key_of: dict[int, str] = {}
    for i, o in enumerate(orders):
        oid = _order_id(o)
        key_of[id(o)] = oid or f"#{i}"
        if oid and oid not in by_id:
            by_id[oid] = o
    claimed: set[str] = set()
    assigned: dict[int, dict] = {}      # execution row id → matched broker order

    fuzzy_placed: list[dict] = []
    fuzzy_error: list[dict] = []
    for r in rows:
        bound = r["_pj"].get("_concord_broker_order_id") \
            or ((r["_pj"].get("data") or {}).get("order") or {}).get("id")
        if bound:
            # A row already carrying a broker order id matches ONLY that order —
            # no fuzzy fallback (its order paging out of the feed ≠ a new match).
            o = by_id.get(str(bound))
            if o is not None and key_of[id(o)] not in claimed:
                claimed.add(key_of[id(o)])
                assigned[r["id"]] = o
        elif r["status"] == "placed":
            fuzzy_placed.append(r)
        else:
            fuzzy_error.append(r)

    def _greedy(pool: list[dict]) -> None:
        cands: list[tuple[float, dict, dict]] = []
        for r in pool:
            ets = _num(r.get("created_at"))
            if ets is None:
                continue
            rdate = _et_date(ets)
            for ots, o in idx.get((str(r["symbol"]).upper(), str(r["side"]).lower()), []):
                dt = abs(ots - ets)
                if dt <= _MATCH_TOLERANCE_S and _et_date(ots) == rdate:
                    cands.append((dt, r, o))
        cands.sort(key=lambda c: c[0])
        taken: set[int] = set()
        for _dt, r, o in cands:
            k = key_of[id(o)]
            if k in claimed or r["id"] in taken:
                continue
            claimed.add(k)
            taken.add(r["id"])
            assigned[r["id"]] = o

    _greedy(fuzzy_placed)
    _greedy(fuzzy_error)

    def _promotion_agrees(r: dict, pj: dict, order: dict, filled: float) -> bool:
        """Economic gate on promoting a fuzzy-matched 'error' row: the order's
        size must agree with the row's own recorded intent — otherwise the
        match is some other cycle's order and promotion would double-count the
        ledger."""
        if r["side"] == "buy":
            amt = _num(r.get("dollar_amount"))
            oamt = _order_amount(order)
            return (amt is not None and oamt is not None
                    and abs(oamt - amt) <= max(0.02, 0.01 * amt))
        est = _num(pj.get("_concord_est_qty"))
        if est is not None:
            return abs(filled - est) <= max(1e-6, 0.01 * est)
        # No recorded size at all: accept only when this order is the sole
        # same-date (symbol, side) candidate not claimed by another row.
        ets = _num(r.get("created_at"))
        if ets is None:
            return False
        rdate = _et_date(ets)
        okey = key_of[id(order)]
        n = 0
        for ots, o in idx.get((str(r["symbol"]).upper(), str(r["side"]).lower()), []):
            if _et_date(ots) != rdate:
                continue
            k = key_of[id(o)]
            if k == okey or k not in claimed:
                n += 1
        return n == 1

    oldest_ts = min((ts for cands in idx.values() for ts, _o in cands), default=None)
    now = time.time()
    updates: list[tuple] = []          # (place_json, status, error_or_None, id) to write
    credits: list[float] = []          # slice_carry credits for aged-out buys
    for r in rows:
        pj = r["_pj"]
        order = assigned.get(r["id"])
        if order is None:
            # No claimable agentic order → do NOT guess. An estimate-based
            # placed row keeps its estimate; a fresh error row stays for A4 /
            # the next cycle. A dead error row past the age-out window is
            # retired here — the feed read SUCCEEDED, so absence is evidence
            # (a BUY additionally needs the feed to provably cover the row's
            # window, since pagination is bounded and the broker may have
            # accepted an order the feed no longer shows).
            # Absence is only evidence when the feed provably reaches back past
            # the row: _fetch_agentic_orders returns [] on any payload shape it
            # does not recognise and pages at most _MAX_ORDER_PAGES, so an
            # "empty" read is not the same as "no such order". Both sides need
            # that proof; only a BUY additionally waits for its session to be
            # past re-placing, because only a buy returns dollars to the slice.
            if (r["status"] == "error"
                    and (_num(r.get("created_at")) or now) < now - _ERROR_AGE_OUT_S
                    and oldest_ts is not None
                    and oldest_ts < (_num(r.get("created_at")) or 0)
                    and (r["side"] == "sell" or _session_retired(r["session_id"]))):
                pj["_concord_aged_out"] = True
                amt = _num(r.get("dollar_amount"))
                if r["side"] == "buy" and amt and not _is_repair_row(r):
                    # The buy never landed: the dollars sat undeployed, so they
                    # return to the member's carry — exactly once (the aged-out
                    # marker is the idempotency guard).
                    pj.pop("_concord_awaiting_credit", None)
                    pj["_concord_carry_credited"] = round(amt, 2)
                    credits.append(round(amt, 2))
                    out["carry_credited"].append(
                        {"symbol": r["symbol"], "amount": round(amt, 2),
                         "session_id": r["session_id"]})
                new_err = ("aged out: no broker match — superseded by a later cycle; "
                           + (r.get("error") or ""))
                updates.append((json.dumps(pj), "skipped", new_err, r["id"]))
                out["aged"].append({"symbol": r["symbol"], "side": r["side"],
                                    "session_id": r["session_id"]})
            else:
                out["unmatched"] += 1
            continue
        state = _order_state(order)
        oid = _order_id(order)

        if state in _FILLED_STATES:
            real = _order_filled_qty(order)
            if real is None or real <= 0:
                out["pending"] += 1
                continue
            if r["status"] == "error" and not _promotion_agrees(r, pj, order, real):
                out["unmatched"] += 1
                continue
            data = pj.setdefault("data", {})
            odict = data.setdefault("order", {})
            prev = _num(odict.get("cumulative_quantity")) or 0.0
            if (pj.get("_concord_reconciled") and prev == real
                    and str(odict.get("state") or "").lower() == state):
                out["unchanged"] += 1   # already fill-true — write nothing
                continue
            odict["cumulative_quantity"] = real
            odict["state"] = state
            pj["_concord_reconciled"] = True
            if oid:
                pj["_concord_broker_order_id"] = oid   # later passes re-bind exactly
            new_status = "placed" if r["status"] == "error" else r["status"]
            updates.append((json.dumps(pj), new_status, None, r["id"]))
            entry = {"symbol": r["symbol"], "side": r["side"], "est_or_prev": round(prev, 8),
                     "real": round(real, 8), "session_id": r["session_id"]}
            if r["status"] == "error":
                out["promoted"].append(entry)
            else:
                out["reconciled"].append(entry)
        elif state in _DEAD_STATES:
            # "Dead" is not the same as "never filled": a day order can fill in
            # PART and have its remainder cancelled at the close. Whatever
            # filled is real — keep it, count it, and only ever hand back the
            # dollars we can prove never reached the market.
            partial = _order_filled_qty(order) or 0.0
            data = pj.setdefault("data", {})
            odict = data.setdefault("order", {})
            odict["cumulative_quantity"] = partial if partial > 0 else 0.0
            odict["state"] = state
            pj["_concord_reconciled"] = True
            if oid:
                pj["_concord_broker_order_id"] = oid
            amt = _num(r.get("dollar_amount"))
            unfilled = 0.0          # dollars provably still in the account
            if r["side"] == "buy" and amt and not _is_repair_row(r):
                if partial <= 0:
                    unfilled = round(amt, 2)
                else:
                    # The filled dollars come from the order's own executions
                    # (or cumulative × average) — the feed carries no
                    # `executed_notional` field, so reading one credited $0 on
                    # every partial fill. Without a trustworthy figure the
                    # split is a guess, and guessing HIGH mints money — credit
                    # nothing and let the filled shares speak in club_value.
                    from ..robinhood.feed import executed_notional
                    _exec = executed_notional(order)
                    unfilled = round(max(0.0, amt - _exec), 2) if _exec is not None else 0.0

            if partial > 0:
                # The shares exist, so the row is a real holding either way.
                entry = {"symbol": r["symbol"], "side": r["side"], "state": state,
                         "real": round(partial, 8), "session_id": r["session_id"]}
                if unfilled > 0.01 and _session_retired(r["session_id"]):
                    pj.pop("_concord_awaiting_credit", None)
                    pj["_concord_carry_credited"] = unfilled
                    credits.append(unfilled)
                    out["carry_credited"].append(
                        {"symbol": r["symbol"], "amount": unfilled,
                         "session_id": r["session_id"]})
                elif unfilled > 0.01:
                    # Creditable, but this session could still re-place the
                    # remainder; hold the claim until it cannot (below).
                    pj["_concord_awaiting_credit"] = unfilled
                updates.append((json.dumps(pj), "placed", None, r["id"]))
                (out["promoted"] if r["status"] == "error" else out["reconciled"]).append(entry)
                continue

            if r["status"] == "placed":
                # Never filled, so the ledger must stop claiming the shares NOW —
                # a phantom holding is what lets a later exit reach into the
                # member's personal position. The dollars are a separate
                # question: crediting them while this session can still re-place
                # the name would deploy the same money twice, so the claim is
                # parked on the row. A live re-run deletes this 'skipped' row
                # (and its claim) and re-places the order — exactly right; if no
                # re-run ever comes, the sweep below pays it out.
                if unfilled > 0.01:
                    if _session_retired(r["session_id"]):
                        pj.pop("_concord_awaiting_credit", None)
                        pj["_concord_carry_credited"] = unfilled
                        credits.append(unfilled)
                        out["carry_credited"].append(
                            {"symbol": r["symbol"], "amount": unfilled,
                             "session_id": r["session_id"]})
                    else:
                        pj["_concord_awaiting_credit"] = unfilled
                updates.append((json.dumps(pj), "skipped", None, r["id"]))
                out["demoted"].append({"symbol": r["symbol"], "side": r["side"],
                                       "state": state, "session_id": r["session_id"]})
            else:
                # An 'error' row whose order the broker positively killed did not
                # fill — but retiring it and returning its dollars are ONE
                # decision, not two. While the session can still re-place the
                # name, a re-run would deploy those dollars again, so we neither
                # credit NOR retire: the row stays 'error' and this branch
                # re-evaluates it on a later pass (a retired row would be skipped
                # forever, and its dollars would simply vanish from the slice).
                if not _session_retired(r["session_id"]):
                    out["pending"] += 1
                    continue
                pj["_concord_aged_out"] = True
                if unfilled > 0.01:
                    pj.pop("_concord_awaiting_credit", None)
                    pj["_concord_carry_credited"] = unfilled
                    credits.append(unfilled)
                    out["carry_credited"].append(
                        {"symbol": r["symbol"], "amount": unfilled,
                         "session_id": r["session_id"]})
                new_err = (f"verified dead at broker ({state}); " + (r.get("error") or ""))
                updates.append((json.dumps(pj), "skipped", new_err, r["id"]))
                out["aged"].append({"symbol": r["symbol"], "side": r["side"],
                                    "session_id": r["session_id"], "state": state})
        else:
            out["pending"] += 1  # queued/confirmed/new — still working, try later

    # Parked claims: dollars from a killed order, demoted immediately so the
    # ledger stopped counting phantom shares, but not creditable while their
    # session could still re-place them. A live re-run deletes the row (and its
    # claim) and re-places the order; if no re-run ever comes, pay it here.
    for r in rows:
        pj = r["_pj"]
        claim = _num(pj.get("_concord_awaiting_credit"))
        if not claim or claim <= 0.01 or pj.get("_concord_carry_credited"):
            continue
        if not _session_retired(r["session_id"]):
            continue
        pj.pop("_concord_awaiting_credit", None)
        pj["_concord_carry_credited"] = round(claim, 2)
        credits.append(round(claim, 2))
        out["carry_credited"].append({"symbol": r["symbol"], "amount": round(claim, 2),
                                      "session_id": r["session_id"]})
        updates.append((json.dumps(pj), r["status"], None, r["id"]))

    if (updates or credits) and not dry_run:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for pj_str, status, err, rid in updates:
                    conn.execute(
                        "UPDATE executions SET place_json=?, status=?, "
                        "error=COALESCE(?, error) WHERE id=?",
                        (pj_str, status, err, rid),
                    )
                for amt in credits:
                    # carry_pending, NOT slice_carry: the buy leg's write-back
                    # overwrites slice_carry with the cycle's own undeployed
                    # figure, so an asynchronous credit written there is either
                    # erased or (worse) re-added by the next write-back. The
                    # write-back drains this column instead, exactly once.
                    conn.execute(
                        "UPDATE member_settings SET carry_pending=COALESCE(carry_pending,0)+? "
                        "WHERE member_id=?", (amt, member_id))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    out["changed"] = len(updates)
    return out




# ledger_trueup refuses to zero more than this share of a member's derived names
# in one sweep (once at least _TRUEUP_MASS_MIN of them) unless force=True: a
# phantom is a name or two of estimate drift, never half the book at once.
_TRUEUP_MASS_FRAC = 0.5
_TRUEUP_MASS_MIN = 5


def ledger_trueup(member_id: int, *, client=None, account_number: str | None = None,
                  dry_run: bool = True, force: bool = False) -> dict:
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
    in _concord_est_qty — history is never edited; positions.trueup_shares folds
    these rows into club_shares_derived, so a trued symbol's derived net reads
    zero and the phantom is never re-detected. Guards: live position must be
    exactly absent (never "small"); any symbol with an execution row in the last
    48h is skipped so an in-flight exit whose fill has not reached the feed yet
    is left alone; and prior TRUEUP coverage is netted off the detected phantom
    before inserting (defense-in-depth: even if the derived source goes blind
    again, the same phantom can never be zeroed twice). ref_id carries the date,
    so a rerun today is idempotent while a future re-drift can be trued again.
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

    # A TRUEUP row is append-only and folds into every view of the club
    # position, so one wrong live reading would erase real holdings from the
    # ledger for good. Two plausibility gates before anything is written:
    # an account that reads EMPTY while the club holds names is a read we
    # cannot trust (a paged/renamed/failed positions envelope, not a member
    # who sold everything — that case is the admin's to force); and a sweep
    # that would zero most of the book at once is the same thing in a
    # smaller hat.
    candidates = [sym for sym, qty in derived.items() if qty > 1e-9 and live.get(sym, 0.0) <= 0]
    if candidates and not force:
        reason = None
        if not live:
            reason = f"account reads empty while the club holds {len(derived)} names"
        elif (len(candidates) >= _TRUEUP_MASS_MIN
              and len(candidates) > _TRUEUP_MASS_FRAC * len(derived)):
            reason = f"{len(candidates)} of {len(derived)} club names read as phantoms"
        if reason:
            audit("system:reconcile", "trueup_refused", str(member_id),
                  {"reason": reason, "candidates": sorted(candidates)[:30], "dry_run": dry_run})
            return {"ok": False, "member_id": member_id, "dry_run": dry_run, "zeroed": [],
                    "refused": reason}
    # Corroboration, per name: shares that are gone from the account left it
    # through a SELL — the feed shows one (any agent; a member's own
    # liquidation is the usual phantom source) newer than the last buy. A
    # name whose latest order is a BUY did not leave; it is a ticker the
    # broker now reports under another symbol, or a positions read that
    # missed it — and zeroing it would hide real shares from every sell leg.
    from ..robinhood.feed import order_ts
    last_event: dict[str, tuple[float, str]] = {}
    for o in positions._fetch_full_order_feed(client, account_number):
        sym = str(o.get("symbol") or "").upper()
        side = str(o.get("side") or "").lower()
        ts = order_ts(o)
        if not sym or side not in ("buy", "sell") or ts is None:
            continue
        if positions._order_filled_qty(o) <= 0:
            continue
        if sym not in last_event or ts >= last_event[sym][0]:
            last_event[sym] = (ts, side)
    uncorroborated: list[str] = []

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
            if last_event.get(sym, (0.0, "buy"))[1] != "sell":
                uncorroborated.append(sym)     # no sell behind the disappearance — see above
                continue
            recent = conn.execute(
                "SELECT 1 FROM executions WHERE member_id=? AND symbol=? AND created_at>=? LIMIT 1",
                (member_id, sym, cutoff)).fetchone()
            if recent:
                continue                       # in-flight exit — leave for the feed
            # Net off what earlier TRUEUP rows already corrected (sells cover,
            # compensating repair buys un-cover) — a phantom is only trued up
            # for the portion no prior correction accounts for.
            coverage = 0.0
            for p in conn.execute(
                    "SELECT side, place_json FROM executions WHERE ref_id LIKE ?",
                    (f"TRUEUP:{member_id}:{sym}:%",)).fetchall():
                try:
                    est = float(json.loads(p["place_json"] or "{}").get("_concord_est_qty") or 0.0)
                except Exception:  # noqa: BLE001
                    est = 0.0
                coverage += est if p["side"] == "sell" else -est
            if qty - coverage <= 1e-9:
                continue                       # already covered — never zero twice
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
    if uncorroborated:
        audit("system:reconcile", "trueup_uncorroborated", str(member_id),
              {"symbols": sorted(uncorroborated)[:30], "dry_run": dry_run})
    if zeroed and not dry_run:
        audit("system:reconcile", "ledger_trueup", str(member_id),
              {"zeroed": zeroed[:30], "count": len(zeroed)})
    return {"ok": True, "member_id": member_id, "dry_run": dry_run, "zeroed": zeroed,
            "uncorroborated": sorted(uncorroborated)}


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
