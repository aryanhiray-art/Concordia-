"""Per-member executor — builds and (optionally) places the consensus basket.

Sizing: each opted-in member buys every consensus pick with an equal
dollar slice of their budget, where
    budget   = min(committed stake resolved vs TOTAL account value,
                   daily_cap_usd if set, MAX_NOTIONAL_PER_MEMBER)
    per_pick = budget / N
(the clamps live in alloc.rebalance_budget so every caller gets them)
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
import os
import time
import uuid

from ..config import settings
from ..db import audit, db, is_armed
from ..robinhood import vault
from ..robinhood.client import RHAuthError
from . import alloc, positions, reconcile, voting

# How old a 'closed' session may be and still count as "the basket sold
# yesterday". A cycle's sell and buy legs are consecutive TRADING days, so the
# gap is at most a long weekend plus a public holiday. Beyond that the session
# is stuck, not pending.
STALE_BASKET_DAYS = 5


def _session_age_days(session: dict) -> int | None:
    """Calendar days between a session's trade_date and today (ET). None when
    the row carries no parseable date — callers then skip the staleness gate
    rather than refusing to trade on a formatting quirk."""
    import datetime as _dt

    from ..calendar_us import today_et
    raw = str(session.get("trade_date") or "").strip()
    try:
        y, m, d = (int(x) for x in raw.split("-"))
        return (today_et() - _dt.date(y, m, d)).days
    except Exception:  # noqa: BLE001
        return None

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
            "       s.daily_cap_usd, s.max_orders, a.account_number, a.agentic_allowed, a.status AS rh_status "
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


# ─── duplicate-buy recovery (A4) ─────────────────────────────────────────────
# A live buy that raises AFTER the broker accepted it is recorded status='error';
# _clear_replanable then deletes that row and a re-run would re-place full size.
# So: capture the errored buy keys BEFORE the clear, and before re-placing any
# of them verify against the broker's own recent orders. Never re-place blind.

def _error_buy_rows(session_id: int) -> dict[tuple[int, str], dict]:
    """(member_id, symbol) → {created_at, dollar_amount} for this session's
    errored BUY rows. Must run BEFORE _clear_replanable deletes them."""
    with db() as conn:
        rows = conn.execute(
            "SELECT member_id, symbol, dollar_amount, created_at FROM executions "
            "WHERE session_id=? AND side='buy' AND status='error'",
            (session_id,),
        ).fetchall()
    return {(r["member_id"], r["symbol"]): {"created_at": r["created_at"],
                                            "dollar_amount": _num(r["dollar_amount"])}
            for r in rows}


def _fetch_recent_orders(client, account_number: str) -> list[dict]:
    """The member's recent AGENTIC equity orders, normalized to a flat list.

    CRITICAL: request placed_agent='agentic' and drop anything not agentic — the
    club/personal safety boundary (mirrors reconcile._fetch_agentic_orders). A
    member's personal ('user') order must NEVER reach A4 buy-recovery, or a coincidental
    personal buy could be matched into a phantom club holding and later market-sell the
    member's PERSONAL shares. Payload shape handled defensively.
    """
    payload = client.get_equity_orders(account_number, placed_agent="agentic")
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        orders = data.get("orders") or data.get("results") or []
    elif isinstance(data, list):
        orders = data
    else:
        orders = []
    return [o for o in orders if isinstance(o, dict) and o.get("placed_agent") in (None, "agentic")]


def _order_ts(order: dict) -> float | None:
    """Best-effort epoch seconds for a broker order's creation time."""
    import datetime
    for key in ("created_at", "created_time", "updated_at"):
        v = order.get(key)
        if v is None:
            continue
        n = _num(v)
        if n is not None:
            return n
        try:
            return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def _order_amount(order: dict) -> float | None:
    """Best-effort dollar amount of a broker order (dollar-denominated buys)."""
    for key in ("dollar_amount", "amount", "notional", "total_notional", "executed_notional"):
        v = order.get(key)
        if isinstance(v, dict):
            v = v.get("amount")
        n = _num(v)
        if n is not None:
            return n
    return None


def _match_recovered_order(orders: list[dict], ref_id: str, symbol: str, side: str,
                           window_start: float, amount: float | None,
                           window_end: float | None = None) -> dict | None:
    """The broker order that IS our errored place, if any.

    Match on our deterministic ref_id when the MCP echoes it; otherwise on
    symbol+side with a created time inside [window_start, window_end] and a dollar
    amount within $0.02 of what we tried to place. The upper time bound keeps a LATER
    personal buy of the same name/amount from being fuzzy-matched as our errored order.
    Only agentic (club) orders are considered — `orders` is already agentic-scoped.
    """
    for o in orders:
        oref = o.get("ref_id") or o.get("client_order_id")
        if oref and str(oref) == ref_id:
            return o
    for o in orders:
        if o.get("placed_agent") not in (None, "agentic"):
            continue                                     # never fuzzy-match a personal order
        if str(o.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(o.get("side") or "").lower() != side:
            continue
        ts = _order_ts(o)
        if ts is None or ts < window_start or (window_end is not None and ts > window_end):
            continue
        if amount is not None:
            oamt = _order_amount(o)
            if oamt is None or abs(oamt - amount) > 0.02:
                continue
        return o
    return None


def run(session: dict, force_dry_run: bool = False, phase: str = "both") -> dict:
    """Execute the session's basket. `phase` = 'sell' (exit dropped names + trim
    overweights), 'buy' (top each name up to target), or 'both'. Splitting sell
    and buy across two trading days lets a cash account settle (T+1) between them."""
    if session["status"] == "open":
        raise ValueError("session is still open — close/tally first")
    picks = [p["symbol"] for p in voting.session_picks(session)]
    if not picks:
        return {"ok": True, "note": "no consensus picks — nothing to execute", "orders": []}

    armed = is_armed()
    will_place = settings.is_live and armed and not force_dry_run
    # Human-quorum floor (finding #10): never spend real money on a basket chosen
    # without human participation. If fewer than MIN_HUMAN_VOTERS humans voted
    # this session, drop to review-only so the ~20 bots can't auto-buy a basket on
    # a zero/low-human-turnout day.
    quorum_blocked = False
    if will_place and voting.human_voter_count(session["id"]) < settings.min_human_voters:
        will_place, quorum_blocked = False, True
    # Calendar-coverage guard (finding #17): if the hand-maintained holiday table
    # has lapsed past its last covered year we can't know today is a trading day —
    # refuse to place REAL orders (review-only) rather than risk trading a closed
    # market. Extend calendar_us._HOLIDAYS to clear this.
    from ..calendar_us import calendar_covers, today_et as _today_et
    calendar_stale = False
    if will_place and not calendar_covers(_today_et()):
        will_place, calendar_stale = False, True
    base = "LIVE" if will_place else ("DRY_RUN(disarmed)" if settings.is_live else "DRY_RUN")
    mode = f"{base}·{phase}"
    sid = session["id"]

    # Reconcile prior fills to TRUTH before clearing/planning: promotes any order
    # that errored on our side but actually FILLED at the broker to 'placed' (so
    # an errored SELL is captured, not silently re-attempted — finding #2; the
    # errored-buy case A4 handled at run time), rewrites estimate quantities to
    # real fills so club_shares is fill-based (the "not all shares sold" fix), and
    # demotes broker-rejected 'placed' rows. Best-effort; never blocks a run.
    try:
        reconcile.reconcile_all()
    except Exception:  # noqa: BLE001
        pass
    # A4: remember which buys ERRORED in a prior run of this session — the broker
    # may have accepted them — before _clear_replanable deletes the evidence.
    prior_error_buys = _error_buy_rows(sid)
    _clear_replanable(sid)
    already = _placed_keys(sid)
    summary = {"mode": mode, "phase": phase, "trade_date": session["trade_date"], "picks": picks,
               "will_place": will_place, "members": [], "orders": []}
    if quorum_blocked:
        summary["quorum_blocked"] = True
        summary["note"] = (f"human quorum not met (< {settings.min_human_voters} human voter(s)) — "
                           f"review only, no real orders placed")
    if calendar_stale:
        summary["calendar_stale"] = True
        summary["note"] = ("market-holiday calendar has lapsed — review only; extend "
                           "calendar_us._HOLIDAYS before trading resumes")
    audit("system", "execute_start", session["trade_date"],
          {"mode": mode, "picks": picks, "will_place": will_place})

    def _skip_member(msum_: dict, reason: str, note: str | None = None) -> None:
        """Record a skipped member: stable skip_reason on the summary row + one
        audit entry, so a member silently missing from execution is explainable."""
        msum_["skip_reason"] = reason
        if note is not None:
            msum_["note"] = note
        summary["members"].append(msum_)
        audit("system", "member_skipped", session["trade_date"],
              {"member_id": msum_["member_id"], "reason": reason})

    # Opted-OUT humans get a summary row too (bots excluded) — visibility only,
    # they are never processed.
    with db() as conn:
        opted_out = conn.execute(
            "SELECT m.id AS member_id, m.display_name FROM members m "
            "LEFT JOIN member_settings s ON s.member_id=m.id "
            "WHERE m.is_bot=0 AND COALESCE(s.opted_in,0)=0 ORDER BY m.id"
        ).fetchall()
    for r in opted_out:
        _skip_member({"member_id": r["member_id"], "name": r["display_name"],
                      "orders": [], "note": "opted out"}, "opted_out")

    from ..db import kv_get
    members = _opted_members()
    for m_idx, m in enumerate(members):
        if kv_get("halt") == "1":
            summary["halted"] = True
            audit("system", "execute_halted", session["trade_date"], None)
            for hm in members[m_idx:]:
                _skip_member({"member_id": hm["member_id"], "name": hm["display_name"],
                              "orders": [], "note": "halted"}, "halted")
            break
        mid = m["member_id"]
        msum = {"member_id": mid, "name": m["display_name"], "orders": [], "note": None}
        if not m.get("account_number") or not m.get("agentic_allowed"):
            _skip_member(msum, "not_connected", "no connected agentic Robinhood account")
            continue
        if m.get("rh_status") == "error":
            _skip_member(msum, "rh_error_stranded",
                         "Robinhood connection stranded in 'error' — admin rh-reset or re-OAuth")
            continue
        if m.get("rh_status") != "connected":
            _skip_member(msum, "not_connected", "no connected agentic Robinhood account")
            continue

        def _portfolio(mid_: int = mid, acct: str = m["account_number"]):
            """One portfolio fetch attempt; returns (client, payload)."""
            c = vault.ensure_client(mid_)
            return c, c.get_portfolio(acct)

        # A5: auth failures strand the member (needs re-OAuth / rh-reset); anything
        # else is treated as transient — one retry, then skip THIS RUN only,
        # leaving rh_status untouched so the next run tries again.
        try:
            try:
                client, port = _portfolio()
            except RHAuthError:
                raise
            except Exception:  # noqa: BLE001  transient/unknown → one retry after 2s
                time.sleep(2)
                client, port = _portfolio()
        except RHAuthError as e:
            vault.mark_status(mid, "error")
            _skip_member(msum, "rh_error_stranded",
                         f"portfolio fetch failed: {type(e).__name__}: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            msum["detail"] = f"{type(e).__name__}: {e}"
            _skip_member(msum, "transient_rh_error",
                         "transient portfolio error — will retry next run")
            continue
        pdata = port.get("data", port) if isinstance(port, dict) else {}
        bp = pdata.get("buying_power") or (pdata.get("buying_power") or {})
        buying_power = _num(bp.get("buying_power") if isinstance(bp, dict) else bp) or 0.0
        total_value = (_num(pdata.get("total_value")) or _num(pdata.get("equity_value"))
                       or buying_power)

        # Rebalance target = committed stake (frees cash via sells), NOT buying power.
        budget = alloc.rebalance_budget(m["alloc_type"], m["alloc_value"], total_value, buying_power,
                                        daily_cap_usd=m.get("daily_cap_usd"))
        # What the CLUB holds for this member — the sell candidate set + upper bound.
        # "derived" reconstructs it from the broker's agentic order feed (drift-proof,
        # personal-safe); on an incomplete feed read we FAIL CLOSED (skip this member,
        # retry next cycle) — never fall through to an empty `held`, which would strand
        # club sells or re-buy the whole basket.
        if settings.club_position_source == "derived":
            try:
                held = positions.club_shares_derived(mid, client=client, account_number=m["account_number"])
            except positions.DerivationIncomplete as e:
                _skip_member(msum, "transient_rh_error",
                             f"club position feed incomplete — skipping this run: {e}")
                continue
        else:
            held = positions.club_shares(mid)        # legacy ledger path (default; rollback)
        # No meaningful stake and nothing club-held to unwind → skip the member.
        # (A budget under $1 with live club holdings still runs, so a member who
        # zeroes their allocation gets liquidated rather than stranded.)
        if budget < 1.00 and not held:
            msum["buying_power"] = round(buying_power, 2)
            msum["budget"] = round(budget, 2)
            _skip_member(msum, "no_stake", "stake below $1 — nothing to deploy")
            continue
        # Broker minimum is $1/order, so we can hold at most floor(budget) names at
        # equal weight. With a small stake the member holds the TOP names only.
        n_eff = max(1, min(len(picks), int(budget // 1.00)))
        basket = picks[:n_eff]
        per_pick = math.floor((budget / n_eff) * 100) / 100.0
        want = set(basket)
        # Live account holdings — the source of truth for BOTH the sell cap (never
        # request more than actually held) AND buy sizing (below). Use the STRICT
        # variant: if the positions read FAILS, we must NOT treat it as an empty
        # account (buy sizing would re-buy the full basket) — skip the member this
        # run and let the next cycle retry. Fails safe on both sell and buy.
        try:
            live = {p["symbol"]: p["quantity"] for p in positions.live_positions_strict(mid)}
        except Exception:  # noqa: BLE001
            _skip_member(msum, "transient_rh_error", "could not read live positions — skipping this run")
            continue
        prices = _quotes(client, list(want | set(held)))
        msum["buying_power"] = round(buying_power, 2)
        msum["allocation"] = round(budget, 2)
        msum["budget"] = round(budget, 2)
        msum["per_pick"] = per_pick
        order_count = 0
        # Per-member order ceiling = the tighter of the global cap and this
        # member's own max_orders (finding #35 — the column was fetched but the
        # global was always used).
        member_max_orders = min(settings.max_orders, int(m.get("max_orders") or settings.max_orders))

        # ── SELL phase: exit dropped names + trim overweight basket names ──
        # Sells come ONLY from `held` (club-bought, reconstructed from executions),
        # so a member's own non-club positions are never touched. A name that left
        # the basket is fully sold; a name still in the basket but OVER its equal-
        # weight target is trimmed back to target, so the proceeds redistribute
        # into the rest of the new basket.
        for symbol, qty in (sorted(held.items()) if phase in ("sell", "both") else []):
            price = prices.get(symbol)
            # Reconcile the club ledger to the LIVE account BEFORE sizing. club_shares
            # is an ESTIMATE (dollars/price at placement, never trued-up to the real
            # fill) that drifts far above reality across buy/sell cycles, so sizing a
            # trim off `qty` alone computes a bogus "excess" and drains the whole real
            # position down to dust. Cap the held quantity to what the account actually
            # shows: never more than the club bought (qty), never more than is there
            # (live). This also drops ledger phantoms (qty>0 but the account holds none).
            held_qty = min(qty, live.get(symbol, 0.0))
            if held_qty <= 0:
                continue
            if symbol in want:
                if price is None:
                    continue                          # can't size a trim without a price
                excess = held_qty - (per_pick / price)   # shares above the equal-weight target
                if excess * price < 1.00:             # not meaningfully overweight → keep
                    continue
                sell_qty, why = excess, "trim to target"
            else:
                sell_qty, why = held_qty, "exited basket"  # full exit of a dropped name
            if (mid, symbol, "sell") in already:
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "placed",
                                       "note": "already sold"})
                continue
            ref_id = ref_id_for(sid, mid, f"SELL:{symbol}")
            sell_qty = min(sell_qty, live.get(symbol, 0.0))   # cap to shares actually held
            # A7: on a FULL exit, sweep a DUST residual (≤0.001sh or ≤$1) up to the
            # live quantity so the position closes cleanly — but NEVER sweep a real
            # personal position (a ≥$1 residual), so a member's own shares are safe.
            sweep = False
            if why == "exited basket":
                live_qty = live.get(symbol, 0.0)
                residual = live_qty - sell_qty
                if residual > 0 and (residual <= 0.001
                                     or (price is not None and residual * price <= 1.00)):
                    sell_qty, sweep = live_qty, True
                    why = "exited basket · dust swept"
            # Robinhood REJECTS a fractional order quantity that carries any non-zero
            # digit beyond the 6th decimal ("Order quantity cannot include fractional
            # shares"), so EVERY sell floors to 6dp. The old 8dp full-exit (meant to
            # avoid leaving a ~1e-6 crumb) instead hard-rejected the ENTIRE order for
            # any holding whose live balance had >6 decimals — stranding the whole
            # position on every cycle (AOS/ALLE/BLDR). A 6dp floor leaves at most a
            # ~$0.0001 crumb, which dust_cleanup / the next exit sweeps.
            qfloor = math.floor(sell_qty * 1e6) / 1e6
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

        # Only deploy what's actually SETTLED — any buy beyond buying power is
        # deferred (it fills next buy day, once this cycle's sells settle), so we
        # never trip EQUITY_NOT_ENOUGH_BP. Hold back the small cash reserve
        # (finding #28 — deployable() was defined but never used) so a sequence of
        # dollar market orders doesn't exhaust buying power on the last leg.
        bp_left = deployable(buying_power)
        # A4: lazy one-shot fetch of the member's recent broker orders, only when a
        # prior run left an errored buy whose true fate is unknown.
        recent_orders: list[dict] | None = None
        orders_fetched = False
        # ── BUY phase: top each basket name up to its equal-weight target ──
        # need = target − current club value, so names already held aren't re-bought
        # and the proceeds from the sells above fund the newly-added names.
        for symbol in (basket if phase in ("buy", "both") else []):
            if (mid, symbol, "buy") in already:
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed",
                                       "note": "already placed"})
                continue
            prior = prior_error_buys.get((mid, symbol))
            if prior is not None:
                # A4: this buy ERRORED in a prior run — the broker may have accepted
                # it before the exception. Verify against the broker's own orders;
                # NEVER re-place blind.
                ref_id = ref_id_for(sid, mid, symbol)
                if not orders_fetched:
                    orders_fetched = True
                    try:
                        recent_orders = _fetch_recent_orders(client, m["account_number"])
                    except Exception:  # noqa: BLE001
                        recent_orders = None
                if recent_orders is None:
                    _record(sid, mid, symbol, prior.get("dollar_amount"), ref_id, "skipped",
                            error="unverifiable prior error — manual check")
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                           "note": "unverifiable prior error — manual check"})
                    continue
                window_start = session.get("opened_at") or ((prior.get("created_at") or 0) - 3600)
                # Upper bound: the broker order (if it filled) was created just BEFORE
                # we recorded the error — bound the match to that instant + a small skew
                # buffer so a later personal buy of the same name can't be fuzzy-matched.
                window_end = (prior.get("created_at") or int(time.time())) + 300
                matched = _match_recovered_order(recent_orders, ref_id, symbol, "buy",
                                                 window_start, prior.get("dollar_amount"),
                                                 window_end=window_end)
                if matched is not None:
                    place = {"_concord_recovered": True, "data": {"order": matched}}
                    px, amt = prices.get(symbol), prior.get("dollar_amount")
                    if px and amt:
                        place["_concord_est_qty"] = round(amt / px, 6)  # est shares (async fill)
                    _record(sid, mid, symbol, amt, ref_id, "placed", place=place)
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed",
                                           "note": "recovered — prior errored place found at broker"})
                    order_count += 1
                    continue
                # verified absent at the broker → the prior place never landed;
                # fall through to the normal (re-)placement path.
            price = prices.get(symbol)
            if price is None:
                # can't value an existing holding or estimate filled shares → skip,
                # so we never blindly re-buy a name we may already hold.
                _record(sid, mid, symbol, None, ref_id_for(sid, mid, symbol), "skipped",
                        error="no quote for sizing")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": "no quote"})
                continue
            # How much of this name is ALREADY held — read from the live account
            # (buy_sizing_account) so a drifted/stale ledger or a manual sale can't
            # mis-size the top-up; fall back to the club ledger only if disabled.
            held_now = live.get(symbol, 0.0) if settings.buy_sizing_account else held.get(symbol, 0.0)
            held_val = held_now * price
            need = round(per_pick - held_val, 2)
            if need < 1.00:
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "held",
                                       "note": f"at target (${held_val:.2f} ≥ ${per_pick:.2f})"})
                continue
            if need > bp_left + 0.005:                 # not enough SETTLED cash yet → defer
                _record(sid, mid, symbol, need, ref_id_for(sid, mid, symbol), "skipped",
                        error="deferred — beyond settled buying power")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                       "note": "defer · settles next cycle"})
                continue
            bp_left -= need
            if order_count >= member_max_orders:
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

    # Mark 'executed' only once the BUY phase completes the cycle — after the sell
    # phase the session stays 'closed' so the next (buy) day can find + finish it.
    # And only when NO order is left in an unresolved 'error' state, so the next
    # run's reconcile/A4 pass can still capture a buy the broker accepted but our
    # side errored on (finding #13 — an early flip made A4 dead on the cron path).
    if will_place and phase in ("buy", "both"):
        with db() as conn:
            err = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE session_id=? AND status='error'", (sid,)
            ).fetchone()[0]
            if err == 0:
                conn.execute("UPDATE vote_sessions SET status='executed' WHERE id=?", (sid,))
            else:
                summary["executed_deferred"] = f"{err} error row(s) — session kept open for fill recovery"
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
    # Post-trade account verification (fail-safe — NEVER blocks or unwinds a run):
    # read each live account and confirm the cycle's intent actually landed (no
    # errored order left an un-sold position, dropped names are gone). Result rides
    # in the summary + an audit row; a FAIL is the signal an operator/alert watches.
    if will_place:
        try:
            from . import verify
            v = verify.verify_all()
            summary["verification"] = {"verdict": v["verdict"], "report": v["report"],
                                       "members": [{"member_id": m["member_id"], "verdict": m["verdict"],
                                                    "findings": m["findings"]} for m in v["members"]]}
            audit("system", "verify_done", session["trade_date"],
                  {"verdict": v["verdict"]})
            # snapshot for the root-side gpt-5.5 narrator (it reads THIS file, never
            # the concord DB — keeps the isolated user's DB/WAL owned by concord).
            try:
                import json as _json
                import pathlib as _pl
                d = _pl.Path(__file__).resolve().parents[2] / "data"
                d.mkdir(exist_ok=True)
                (d / "verify_last.json").write_text(_json.dumps(
                    {"trade_date": session["trade_date"], "mode": mode,
                     "verdict": v["verdict"], "report": v["report"], "at": int(time.time())}))
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            summary["verification"] = {"verdict": "SKIPPED", "error": f"{type(e).__name__}: {e}"}
    return summary


def dust_cleanup(member_id: int, dry_run: bool = True) -> dict:
    """Sweep sub-$1 crumb positions left on club-traded names (est-vs-fill drift).

    Candidates = live long positions whose symbol appears in the member's club
    execution history (placed orders) AND live value ≤ $1.00 AND quantity < 0.01
    shares — sizes only ever produced by fill drift, never by a real personal
    holding. Each candidate runs the same review→place→record pipeline as run(),
    with a deterministic ref_id keyed 'DUST:{symbol}'. dry_run=True (default)
    reviews and returns the preview WITHOUT recording or placing; a real place
    additionally requires execution_mode=live + ARMED, exactly like run().
    """
    with db() as conn:
        row = conn.execute(
            "SELECT account_number, status FROM rh_accounts WHERE member_id=?", (member_id,)
        ).fetchone()
        club_syms = {r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND status='placed'",
            (member_id,)
        ).fetchall()}
        latest_sid = conn.execute("SELECT COALESCE(MAX(id),0) FROM vote_sessions").fetchone()[0]
    if not row or not row["account_number"] or row["status"] != "connected":
        return {"ok": False, "error": "no connected Robinhood account",
                "member_id": member_id, "orders": []}
    acct = row["account_number"]
    will_place = settings.is_live and is_armed() and not dry_run
    try:
        client = vault.ensure_client(member_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"client unavailable: {type(e).__name__}: {e}",
                "member_id": member_id, "orders": []}
    live = positions.live_positions(member_id)
    prices = _quotes(client, [p["symbol"] for p in live])
    out = {"ok": True, "member_id": member_id, "dry_run": dry_run,
           "will_place": will_place, "orders": []}
    for p in live:
        symbol, qty = p["symbol"], p["quantity"]
        if symbol not in club_syms or qty >= 0.01:
            continue
        price = prices.get(symbol)
        if price is None or qty * price > 1.00:
            continue                                   # can't verify it's dust → leave it
        ref_id = ref_id_for(latest_sid, member_id, f"DUST:{symbol}")
        qfloor = math.floor(qty * 1e8) / 1e8           # full sweep at broker precision
        if qfloor <= 0:
            continue
        qstr = f"{qfloor:.8f}"
        entry = {"symbol": symbol, "side": "sell", "qty": qstr,
                 "value": round(qty * price, 4), "ref_id": ref_id}
        try:
            review = client.review_market_sell(acct, symbol, qstr)
        except Exception as e:  # noqa: BLE001
            if not dry_run:
                _record(latest_sid, member_id, symbol, None, ref_id, "error",
                        error=f"dust review: {e}", side="sell")
            entry.update(status="error", note=str(e))
            out["orders"].append(entry)
            continue
        blocked, alerts = has_blocking_alerts(review)
        if blocked:
            if not dry_run:
                _record(latest_sid, member_id, symbol, None, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert", side="sell")
            entry.update(status="skipped", alerts=alerts)
            out["orders"].append(entry)
            continue
        if not will_place:
            if not dry_run:
                _record(latest_sid, member_id, symbol, None, ref_id, "reviewed",
                        review=review, side="sell")
            entry.update(status="reviewed")
            out["orders"].append(entry)
            continue
        try:
            place = client.place_market_sell(acct, symbol, qstr, ref_id=ref_id)
            if isinstance(place, dict):
                place["_concord_est_qty"] = qfloor     # shares sold (broker fill is async)
            _record(latest_sid, member_id, symbol, None, ref_id, "placed", review=review,
                    place=place, side="sell")
            entry.update(status="placed")
        except Exception as e:  # noqa: BLE001
            _record(latest_sid, member_id, symbol, None, ref_id, "error", review=review,
                    error=f"dust place: {e}", side="sell")
            entry.update(status="error", note=str(e))
        out["orders"].append(entry)
    return out


def run_today(force_dry_run: bool = False) -> dict:
    """Single-flight wrapper around the day's phase run.

    A file lock (finding #8) prevents a timer run and a dashboard 'Execute now'
    (or two quick clicks) from executing concurrently — the check-then-act on
    _placed_keys plus place()-before-record could otherwise let two runs both
    place the same order, relying only on broker ref_id dedup.
    """
    import fcntl
    import tempfile
    lock_path = os.path.join(tempfile.gettempdir(), "concord-execute.lock")
    lf = open(lock_path, "w")  # noqa: SIM115
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return {"ok": False, "note": "another execute run is already in progress", "orders": []}
        try:
            return _run_today_locked(force_dry_run=force_dry_run)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    finally:
        lf.close()


def _run_today_locked(force_dry_run: bool = False) -> dict:
    """Run the phase scheduled for today: SELL on a sell/vote day (close the ballot,
    then exit dropped names), BUY on a buy day (top up the last sold basket with the
    now-settled cash). Idle days do nothing. This is what the cron + the dashboard
    'Execute now' button call."""
    from ..calendar_us import cycle_phase, today_et
    phase = cycle_phase(today_et())
    if phase is None:
        return {"ok": True, "note": "idle day — no sell or buy phase scheduled today", "orders": []}
    if phase == "buy":
        session = voting.latest_session("closed")   # the basket sold on the prior sell day
        if not session:
            return {"ok": True, "note": "no sold basket waiting to be bought", "orders": []}
        # The sell and buy legs are CONSECUTIVE trading days. latest_session()
        # is unbounded, so a session that got stuck at 'closed' stays the newest
        # 'closed' row forever and this leg would happily buy it — with real
        # money, months later.
        #
        # Not hypothetical: session 14 (2026-07-20) never flipped to 'executed'
        # because 3 orders errored, and it was the ONLY 'closed' row in
        # production while the club was live and armed. Any buy-phase run
        # (a second timer fire, the admin Execute-now button, or a buy day whose
        # own cycle never reached 'closed') would have re-bought a basket the
        # club deliberately exited six cycles earlier.
        #
        # A stale basket is never worth buying, so this refuses instead of
        # trading and says so loudly. STALE_BASKET_DAYS spans a long weekend
        # plus a holiday; anything older is a stuck session to be investigated,
        # not an instruction.
        age_days = _session_age_days(session)
        if age_days is not None and age_days > STALE_BASKET_DAYS:
            note = (f"refusing to buy a stale basket: session {session.get('id')} "
                    f"({session.get('trade_date')}) is {age_days} days old and still "
                    f"'closed' — investigate that session; it is not today's cycle")
            print(f"[executor] {note}", flush=True)
            audit("system", "buy_refused_stale_basket", str(session.get("id")),
                  {"trade_date": session.get("trade_date"), "age_days": age_days})
            return {"ok": True, "note": note, "orders": [], "stale_session": session.get("id")}
        return run(session, force_dry_run=force_dry_run, phase="buy")
    # 'sell' or 'both' — close today's ballot, then sell (and buy too, if 'both')
    session = voting.session_for_trade_date(today_et().isoformat())
    if not session:
        return {"ok": True, "note": "no session for today", "orders": []}
    if session["status"] == "open":
        session = voting.close_and_select(session["id"])
    return run(session, force_dry_run=force_dry_run, phase=phase)
