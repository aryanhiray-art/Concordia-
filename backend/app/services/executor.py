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
from decimal import ROUND_FLOOR, Decimal
import os
import time
import uuid

from ..config import settings
from ..db import audit, db, is_armed, kv_get
from ..robinhood import vault
from ..robinhood.client import RHAuthError
from . import alloc, membership, positions, reconcile, runreports, voting

# How old a 'closed' session may be and still count as "the basket sold
# yesterday". A cycle's sell and buy legs are consecutive TRADING days, so the
# gap is at most a long weekend plus a public holiday. Beyond that the session
# is stuck, not pending.
STALE_BASKET_DAYS = 5


def _cycle_gap_days() -> int:
    """Calendar days between consecutive cycle sessions under the live cadence.

    The crash-continuation branch looks at the session BEFORE this one, so a
    fixed bound silently becomes dead code whenever the cadence is slower than
    it: on the weekly cadence consecutive sessions are 7 days apart, and a
    5-day window never opens. Values are generous — they span the weekend and a
    holiday that pushes the next cycle day out.
    """
    return {"daily": 4, "alternate": 5, "mwf": 5, "weekly": 10}.get(
        settings.cycle_cadence, 10)

# Both-day buying-power poll: how long the buy leg will wait for Robinhood to
# credit the morning's sale proceeds (fill + ledger lag measured >8s on 08/19),
# and how often it looks.
#
# The early-exit target was 80% of the placed sells' value, which SYSTEMATICALLY
# stranded the tail of every rotation: proceeds are spendable immediately on a
# limited-margin account, so leaving a fifth of them uncredited meant the last
# name (or two) of the basket deferred with "settles next cycle" on a cycle that
# has no settlement wait at all. Observed 2026-08-31: every member deferred their
# final pick(s) while all four accounts were limited_margin. 0.98 leaves only
# rounding/partial-fill slack, and the plateau check below still exits early when
# credit genuinely stops arriving.
BP_POLL_EVERY_S = 6
BP_POLL_MAX_S = 120
BP_POLL_TARGET_FRAC = 0.98
# How many consecutive non-rising polls mean "credit has stopped arriving". One
# was too few: fills land progressively at 09:31, so buying power plateaus
# BETWEEN fills and a single flat read exited the poll before the rest landed.
BP_POLL_FLAT_POLLS = 3

# A position more than this fraction below its target, whose top-up would be
# under the broker's $1 minimum order, is exited and re-bought at the right
# size. Small enough to keep a small stake tracking the basket, large enough
# that cent-level drift does not churn a round trip every cycle. 0.05 was not:
# near-daily ~5% drift round-tripped a full position to close <$1 of drift.
_RESIZE_MIN_FRAC = 0.15


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
            "       s.daily_cap_usd, s.max_orders, COALESCE(s.compound,1) AS compound,"
            "       s.alloc_applied, COALESCE(s.slice_carry,0) AS slice_carry, a.account_number, a.agentic_allowed, a.status AS rh_status "
            "FROM members m "
            "JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id "
            # A DEPARTED member must never be traded. The owner-side removal
            # deliberately leaves their brokerage connection alone (it is their
            # account), so opted_in alone does not exclude them — without this
            # they would lose their vote and keep getting real orders placed.
            # Bots have no brokerage and never will — excluding them here keeps
            # the run report to the humans instead of 20 not_connected rows.
            f"WHERE s.opted_in=1 AND m.is_bot=0 AND {membership.active_clause('m')}"
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
            # isfinite, not just truthiness: json.loads accepts a bare NaN, and a
            # NaN price is TRUTHY and is not None — so it would pass this filter
            # and then walk straight through the fail-closed `if _p is None`
            # valuation guard below, poisoning club_value. Every later comparison
            # against it silently reads False (nan < 1.00, nan > 0), which is the
            # one failure mode that guard exists to prevent. An unusable quote is
            # a MISSING quote: leaving it out routes the name into the
            # 'unquotable_holding' skip the code already models.
            if q.get("symbol") and px and math.isfinite(px):
                out[q["symbol"]] = px
    return out


def already_placed_rows(session_id: int) -> bool:
    """True once this session has any placed order — its record is then final."""
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM executions WHERE session_id=? AND status='placed' LIMIT 1",
            (session_id,)).fetchone()
    return row is not None


def _clear_replanable(session_id: int) -> None:
    """Drop prior non-final rows so a re-run is clean; never touch 'placed' —
    and never 'error': errored-order evidence survives until positively
    resolved (reconcile promotion, supersession, or session abandonment), so
    a re-run can never blindly re-place an order the broker may have taken.
    'superseded' rows are terminal leftovers and may be garbage-collected."""
    with db() as conn:
        conn.execute(
            "DELETE FROM executions WHERE session_id=? AND status IN "
            "('planned','reviewed','skipped','superseded')",
            (session_id,),
        )


def _supersede_error(session_id: int, member_id: int, symbol: str, side: str) -> None:
    """Flip surviving 'error' rows for this key to terminal 'superseded'.

    Called ONLY where a new broker order is actually about to be placed (or where
    the old order has been positively resolved): the stale row must not survive to
    be fuzzy-matched against the NEW order and promoted, double-counting the
    ledger. A review-only path — a dry run, a blocked alert, a review that raised —
    places nothing, so it must leave the evidence alone; the row is the only proof
    that an order the broker may have accepted exists.
    """
    with db() as conn:
        conn.execute(
            "UPDATE executions SET status='superseded' WHERE session_id=? AND "
            "member_id=? AND symbol=? AND side=? AND status='error'",
            (session_id, member_id, symbol, side))


def _hold_strand(session_id: int, member_id: int, will_place: bool) -> None:
    """Record that this member was stranded mid-pass with orders in flight.

    The hold has to outlive the RUN: a terminal auth failure marks the
    connection 'error', so on the next run the member is skipped at the very
    top and that run's summary shows no strand at all. Scanning only the
    current summary would flip the session to 'executed' there, and the sold
    proceeds could never be redeployed. GC'd with the other per-session memos.
    """
    if not will_place:
        return
    with db() as conn:
        conn.execute("INSERT INTO kv(key,value) VALUES(?,'1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'",
                     (f"strand_hold:{session_id}:{member_id}",))


def _clear_strand(session_id: int, member_id: int) -> None:
    """Release the hold once this member's pass completes normally."""
    with db() as conn:
        conn.execute("DELETE FROM kv WHERE key=?",
                     (f"strand_hold:{session_id}:{member_id}",))


def _stranded_members(session_id: int) -> list[int]:
    """Members still holding this session open after a mid-pass strand."""
    with db() as conn:
        rows = conn.execute("SELECT key FROM kv WHERE key LIKE ?",
                            (f"strand_hold:{session_id}:%",)).fetchall()
    out: list[int] = []
    for r in rows:
        try:
            out.append(int(str(r["key"]).rsplit(":", 1)[1]))
        except (IndexError, ValueError):
            continue
    return out


def _retire_stale_sessions(current_sid: int | None = None) -> None:
    """Terminal-age stuck sessions: a 'closed' session older than STALE_BASKET_DAYS
    can never trade (the buy leg refuses it), so it must stop freezing its voters and
    stop feeding error rows to every reconcile pass. Flips remaining 'error' rows to
    'abandoned' (text preserved) and the session to 'lapsed'. Deliberately does
    NOT return an abandoned buy's dollars to the slice: only reconcile reads the
    broker, so only reconcile can prove an order never landed, and crediting one
    that DID land would put the same money in the slice twice. An unreachable
    member's dollars therefore stay out — the conservative direction.
    Also GCs cycle_budget stamps of long-dead sessions. `current_sid` is never retired — a manual run of an
    old session must not lapse the very cycle it is executing. Best-effort, never raises.
    """
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT id, trade_date FROM vote_sessions WHERE status='closed'"
            ).fetchall()
        for r in rows:
            if current_sid is not None and r["id"] == current_sid:
                continue
            age = _session_age_days({"trade_date": r["trade_date"]})
            if age is None or age <= STALE_BASKET_DAYS:
                continue
            with db() as conn:
                cur = conn.execute(
                    "UPDATE executions SET status='abandoned' "
                    "WHERE session_id=? AND status='error'", (r["id"],))
                conn.execute(
                    "UPDATE vote_sessions SET status='lapsed' "
                    "WHERE id=? AND status='closed'", (r["id"],))
            audit("system", "session_lapsed", str(r["id"]),
                  {"trade_date": r["trade_date"], "abandoned_errors": cur.rowcount})
        # Stamp GC: cycle_budget:{sid}:{mid} keys are write-once per session and
        # only ever reread within the cycle — anything 10+ sessions back is dead.
        with db() as conn:
            max_sid = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM vote_sessions").fetchone()[0]
            keys = [r["key"] for r in conn.execute(
                "SELECT key FROM kv WHERE key LIKE 'cycle_budget:%' "
                "OR key LIKE 'carry_drained:%' OR key LIKE 'repair_spend:%' "
                "OR key LIKE 'strand_hold:%'").fetchall()]
            for k in keys:
                try:
                    k_sid = int(k.split(":")[1])
                except (IndexError, ValueError):
                    continue
                if k_sid < max_sid - 10:
                    conn.execute("DELETE FROM kv WHERE key=?", (k,))
    except Exception:  # noqa: BLE001 — retirement is bookkeeping, never blocks a run
        pass


# ─── duplicate-buy recovery (A4) ─────────────────────────────────────────────
# A live buy that raises AFTER the broker accepted it is recorded status='error'.
# The row survives every clear (see _clear_replanable) until positively resolved:
# before re-placing any of them verify against the broker's own recent orders,
# and supersede the stale row first. Never re-place blind.

def _error_buy_rows(session_id: int) -> dict[tuple[int, str], dict]:
    """(member_id, symbol) → {id, ref_id, created_at, dollar_amount} for this
    session's errored BUY rows."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, ref_id, member_id, symbol, dollar_amount, created_at FROM executions "
            "WHERE session_id=? AND side='buy' AND status='error'",
            (session_id,),
        ).fetchall()
    return {(r["member_id"], r["symbol"]): {"id": r["id"], "ref_id": r["ref_id"],
                                            "created_at": r["created_at"],
                                            "dollar_amount": _num(r["dollar_amount"])}
            for r in rows}


# How deep A4 pages the broker's order feed, and how far back it must reach
# before "no match" may be read as "the order never landed". A single page was
# NOT enough: the feed is fetched from inside the buy loop, AFTER the sell leg
# and after this run's earlier buys, so this cycle's own orders crowd page one
# ahead of the older row being verified. reconcile pages the same feed 12 deep.
_A4_MAX_ORDER_PAGES = 12


def _fetch_recent_orders(client, account_number: str) -> tuple[list[dict], bool]:
    """The member's recent AGENTIC equity orders, plus whether the feed was EXHAUSTED.

    CRITICAL: request placed_agent='agentic' and drop anything not agentic — the
    club/personal safety boundary (mirrors reconcile._fetch_agentic_orders). A
    member's personal ('user') order must NEVER reach A4 buy-recovery, or a coincidental
    personal buy could be matched into a phantom club holding and later market-sell the
    member's PERSONAL shares. Payload shape handled defensively.

    Returns (orders, exhausted). `exhausted` is True only when the broker said
    there are no further pages — i.e. absence from this list is real absence.
    When it is False the list was cut short by the page cap, and "not found"
    means "not found YET": a caller must not treat that as proof. Deliberately
    uncached — the executor has to see orders placed seconds ago.
    """
    out: list[dict] = []
    cursor: str | None = None
    seen: set[str] = set()
    exhausted = False
    for _ in range(_A4_MAX_ORDER_PAGES):
        payload = client.get_equity_orders(account_number, cursor=cursor,
                                           placed_agent="agentic")
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
            exhausted = True        # the broker itself said that is everything
            break
        cursor = _cursor_from_next(nxt)
        if cursor is None:
            break                   # a next page we cannot decode: NOT proof of absence
        if cursor in seen:
            break                   # a non-advancing cursor proves nothing either
        seen.add(cursor)
    return out, exhausted


def _cursor_from_next(nxt) -> str | None:
    """The cursor for a feed's next page — a bare opaque token at Robinhood,
    decoded in one place (robinhood.feed) for every feed reader."""
    from ..robinhood.feed import cursor_from_next
    return cursor_from_next(nxt)


# Mark plausibility gate (see the budget branch in _run_impl): a compounding
# member whose visible club slice reads more than this fraction below the
# figure last deployed — while the account itself is still worth at least that
# fraction of it — is not a member whose stocks fell; it is a member whose
# stocks (or cash) the club can no longer SEE. Fail closed rather than ride
# the commitment down to it.
_MARK_DROP_FRAC = 0.25
_MARK_GATE_MIN_USD = 5.0


def _feed_covers(orders: list[dict], exhausted: bool, created_at) -> bool:
    """Can absence from `orders` be read as proof this order never landed?

    Only when the feed provably spans the moment we tried to place it: either
    the broker handed us every page, or the oldest page we did read predates the
    row. Without that, "not in the list" only means the list stopped too early —
    and acting on it would retire a real fill (or re-place a real buy).
    """
    if exhausted:
        return True
    ts = _num(created_at)
    if ts is None:
        return False
    oldest = min((t for t in (_order_ts(o) for o in orders) if t is not None),
                 default=None)
    return oldest is not None and oldest < ts


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
    """Dollar amount of a broker order. Robinhood carries it ONLY under
    dollar_based_amount.amount — decoded in robinhood.feed for every reader
    (the five-key list this used to search never matched a real order, so
    A4's fuzzy match could not find an errored buy that had landed)."""
    from ..robinhood.feed import order_amount
    return order_amount(order)


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


def run(session: dict, force_dry_run: bool = False, phase: str = "both",
        trigger: str = "schedule") -> dict:
    """Execute the session's basket, then persist a run_reports row for it.

    `phase` = 'sell' (exit dropped names + trim overweights), 'buy' (top each
    name up to target), or 'both'. `trigger` names who pulled the lever
    ('schedule' | 'manual:<email>') and rides into the report row. EVERY
    invocation writes a report — even a no-op run (no picks) — and report
    persistence can never fail the run (runreports.persist swallows errors).
    """
    started_at = int(time.time())
    trade_date = session.get("trade_date") if isinstance(session, dict) else None
    try:
        summary = _run_impl(session, force_dry_run=force_dry_run, phase=phase)
    except Exception as e:  # noqa: BLE001 — record the crashed run, then re-raise
        runreports.persist(
            {"ok": False, "error": f"{type(e).__name__}: {e}", "phase": phase,
             "trade_date": trade_date, "orders": []},
            trigger=trigger, started_at=started_at)
        raise
    # No-op early returns (e.g. "no consensus picks") omit identity fields the
    # wrapper knows — backfill them so the report row is still attributable.
    report = dict(summary)
    report.setdefault("trade_date", trade_date)
    report.setdefault("phase", phase)
    runreports.persist(report, trigger=trigger, started_at=started_at)
    # Member trade receipts (G5a) — strictly AFTER the report is persisted, and
    # fully fenced: notification can never fail or delay a run (notify fences
    # every member's email again on its own).
    try:
        from . import notify
        notify.send_run_emails(report)
    except Exception:  # noqa: BLE001
        pass
    return summary


def _run_impl(session: dict, force_dry_run: bool = False, phase: str = "both") -> dict:
    """The actual execution pass — see run() (which owns report persistence)."""
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
    # Thin-basket floor (G2): a near-empty ballot (one human voting one stock)
    # must never become a live 1-2 name "basket" — the sell leg would exit the
    # whole portfolio into a concentration. Below MIN_BASKET_SIZE picks the run
    # downgrades to review-only; dry-runs still simulate.
    thin_basket = False
    if will_place and len(picks) < settings.basket_floor:
        will_place, thin_basket = False, True
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
        reconcile.reconcile_all(dry_run=not will_place)
    except Exception:  # noqa: BLE001
        pass
    # Retire stuck sessions only AFTER reconcile has had its last-chance
    # promotion pass over their error rows, and only on a run that is allowed to
    # move real state — an inspection dry run must not lapse sessions, abandon
    # rows or GC stamps. Best-effort, never raises.
    if will_place:
        _retire_stale_sessions(current_sid=sid)
    # A4: remember which buys ERRORED in a prior run of this session — the
    # broker may have accepted them. The rows survive _clear_replanable; they
    # are resolved below by verification (promote/supersede), never deleted.
    prior_error_buys = _error_buy_rows(sid)
    # A dry run over a cycle that ALREADY PLACED is an inspection, not a
    # re-plan: clearing there overwrote the live run's skipped/reviewed rows
    # with review-only ones and destroyed the audit trail of what the cycle
    # actually deferred (observed 2026-08-24 — a verification dry-run erased
    # why two members' buys did not place; 'placed' rows survived, so only the
    # explanation was lost). A dry run over a cycle that has placed nothing
    # still re-plans freely, which is what planning a cycle needs. 'error'
    # rows are never in the delete list, so no inspection can destroy the
    # recovery evidence either way.
    if will_place or not already_placed_rows(sid):
        _clear_replanable(sid)
    already = _placed_keys(sid)
    summary = {"mode": mode, "phase": phase, "trade_date": session["trade_date"], "picks": picks,
               "will_place": will_place, "members": [], "orders": []}
    if quorum_blocked:
        summary["quorum_blocked"] = True
        summary["note"] = (f"human quorum not met (< {settings.min_human_voters} human voter(s)) — "
                           f"review only, no real orders placed")
    if thin_basket:
        summary["thin_basket"] = True
        summary["note"] = (f"basket too thin ({len(picks)} < {settings.basket_floor}) — "
                           f"review only, no real orders placed; the previous basket holds")
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
    # they are never processed. Active members only: a departed/anonymized
    # member must not appear as an eternal "opted out" row in every report.
    with db() as conn:
        opted_out = conn.execute(
            "SELECT m.id AS member_id, m.display_name FROM members m "
            "LEFT JOIN member_settings s ON s.member_id=m.id "
            f"WHERE m.is_bot=0 AND COALESCE(s.opted_in,0)=0 "
            f"AND {membership.active_clause('m')} ORDER BY m.id"
        ).fetchall()
    for r in opted_out:
        _skip_member({"member_id": r["member_id"], "name": r["display_name"],
                      "orders": [], "note": "opted out"}, "opted_out")

    from ..db import kv_get, kv_set

    def _mature_clamp(msum_: dict, mid_: int, ride: float, eff: float, tol: float) -> float:
        """Over-commitment auto-clamp with maturation: a commitment above the
        account's deployable capacity (club slice + cash) would defer/carry the
        excess every cycle forever. One low reading must not shrink a
        commitment (a transient BP dip or bad read), so the write-down matures
        only on the 2nd consecutive materially-clamped CYCLE — the stored
        session id is what makes it cycles rather than runs, since a split
        cadence and same-day re-runs both re-enter this for one cycle.
        Persisted only on live runs. Returns the commitment figure the
        write-back should honor (never the daily-capped budget: a daily cap
        limits deployment, it never rewrites the commitment)."""
        key = f"clamp_pending:{mid_}"
        try:
            prev = json.loads(kv_get(key) or "{}")
            count, last_sid = int(prev.get("n") or 0), prev.get("sid")
        except (TypeError, ValueError):
            count, last_sid = 0, None
        if ride - eff > tol:
            if last_sid != sid:
                count += 1
            if will_place:
                kv_set(key, json.dumps({"n": count, "sid": sid}))
            if count >= 2:
                msum_["commit_clamped"] = {"from": round(ride, 2), "to": round(eff, 2)}
                return round(eff, 2)
            return round(ride, 2)
        if will_place:
            kv_set(key, json.dumps({"n": 0, "sid": sid}))
        return round(ride, 2)

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
        bp = pdata.get("buying_power")
        _bp_raw = _num(bp.get("buying_power") if isinstance(bp, dict) else bp)
        if _bp_raw is None:
            # STRICT, like total_value below: a renamed/missing field used to
            # read as $0 of buying power — no skip, no audit — which defers
            # every buy and, after two such cycles, MATURES the over-commitment
            # clamp and writes the stake down to the marked slice.
            _skip_member(msum, "transient_rh_error",
                         "unreadable buying power — skipping this run")
            continue
        buying_power = _bp_raw
        # STRICT account value — never fall back to buying_power: a malformed
        # payload collapsing capacity to residual cash would size a near-zero
        # budget and liquidate the member's slice. Unreadable → skip (below).
        tv_raw = _num(pdata.get("total_value"))
        if tv_raw is None:
            tv_raw = _num(pdata.get("equity_value"))

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
        # Quotes for everything we might touch: the full pick list (the basket is
        # sized from the budget BELOW, so it isn't known yet) plus every club
        # holding (valuation + sells).
        prices = _quotes(client, list(set(picks) | set(held)))

        # Mark the CLUB slice from live positions × quotes — feeds the capacity
        # clamp in BOTH budget branches and the account-value cross-check below.
        # A missing quote UNDERVALUES the slice → the shortfall would read as
        # "over target" and trim real positions. Fail closed.
        club_value = 0.0
        unpriced = []
        for _sym, _qty in held.items():
            _q = min(_qty, live.get(_sym, 0.0))
            if _q <= 0:
                continue
            _p = prices.get(_sym)
            if _p is None:
                unpriced.append(_sym)
            else:
                club_value += _q * _p
        if unpriced:
            _skip_member(msum, "unquotable_holding",
                         f"no quote for club valuation ({','.join(unpriced[:3])}) — skipping this run")
            continue
        # Fail closed on the account value: an account cannot be worth
        # materially less than the club positions inside it — a violation is an
        # inconsistent read. Must happen BEFORE any budget stamp is written.
        total_value = tv_raw
        if total_value is None or total_value < club_value - max(0.05 * club_value, 5.0):
            _skip_member(msum, "transient_rh_error",
                         "unreadable/inconsistent account value — skipping this run")
            continue

        # ── budget ──────────────────────────────────────────────────────────
        # COMPOUNDING (default): a dollar stake rides its own P&L in both
        # directions — budget = mark-to-market club value, adjusted by any
        # stake change since the last honored figure. The valuation happens on
        # the SELL day and is stamped to kv, because on the T+1 buy day part of
        # the slice is settling CASH that a positions-only mark can't see.
        # PINNED (compound off) keeps the legacy min(stake, account) target.
        # Compounding applies to BOTH stake types (owner call 2026-08-24): the
        # committed figure itself rides the slice, so "I put in $100 and it grew
        # to $105" leaves $105 committed. A percent stake bootstraps from
        # pct x account and compounds from there like a dollar stake.
        compounding = bool(m.get("compound", 1))
        # A PERCENT stake means "not compounded yet": the percentage specifies
        # the opening amount, is resolved against the account once, and the
        # write-back below converts the member to the dollar figure that was
        # actually deployed. From then on (alloc_type='dollar') the commitment
        # RIDES its own P&L. Bootstrapping a percent member from their current
        # slice instead would lock in whatever the slice had drifted to, which
        # can exceed what they ever committed.
        rides = compounding and m["alloc_type"] == "dollar"
        budget_stamp = f"cycle_budget:{sid}:{mid}"
        stamped_stake = None
        # The commitment figure the write-back should persist. Derived from the
        # UNCAPPED ride vs deployable capacity (never from the daily-capped
        # budget — a daily cap limits deployment, it never rewrites the
        # commitment); None → the write-back falls back to the budget.
        commit_target: float | None = None
        if rides:
            _stamped = kv_get(budget_stamp)
            if _stamped:
                # WRITE-ONCE per session — reused on the T+1 buy day AND on any
                # re-run of a 'both' day. Recomputing after the morning's sells
                # have filled would mark the slice from post-sell positions while
                # the proceeds are still settling CASH: every kept name would read
                # as overweight and get trimmed, permanently shrinking the slice.
                try:
                    _st = json.loads(_stamped)
                    budget = float(_st["budget"])
                    stamped_stake = float(_st["stake"])
                    _cm = _st.get("commit")
                    commit_target = float(_cm) if _cm is not None else None
                except (TypeError, ValueError, KeyError):
                    # Fail CLOSED like every other unreadable input here: a
                    # coerced 0 budget would liquidate the member's whole slice.
                    _skip_member(msum, "transient_rh_error",
                                 "unreadable cycle budget stamp — skipping this run")
                    continue
            else:
                budget = None
                # Crash continuation: a 'both' day that placed its SELLS and
                # then died before any buy leaves the proceeds as cash the
                # fresh positions-only mark below cannot see — the slice would
                # silently shrink by the sold notional. If the immediately
                # previous session left a stamp, never executed, sold for this
                # member and bought nothing, this run IS that interrupted
                # cycle's buy leg: reuse its stamp verbatim (re-clamped), and
                # retire the old stamp so it can never fold forward twice.
                with db() as conn:
                    _prev = conn.execute(
                        "SELECT id, status, trade_date FROM vote_sessions "
                        "WHERE id < ? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
                if _prev is not None and _prev["status"] != "executed":
                    _prev_key = f"cycle_budget:{_prev['id']}:{mid}"
                    _prev_stamp = kv_get(_prev_key)
                    _prev_age = _session_age_days(dict(_prev))
                    if (_prev_stamp and _prev_age is not None
                            and _prev_age <= max(STALE_BASKET_DAYS, _cycle_gap_days())):
                        with db() as conn:
                            _sold = conn.execute(
                                "SELECT COUNT(*) FROM executions WHERE session_id=? "
                                "AND member_id=? AND side='sell' AND status='placed'",
                                (_prev["id"], mid)).fetchone()[0]
                            _bought = conn.execute(
                                "SELECT COUNT(*) FROM executions WHERE session_id=? "
                                "AND member_id=? AND side='buy' AND status='placed'",
                                (_prev["id"], mid)).fetchone()[0]
                        if _sold > 0 and _bought == 0:
                            try:
                                _pst = json.loads(_prev_stamp)
                                _pb = float(_pst["budget"])
                                _ps = float(_pst["stake"])
                                _pc = _pst.get("commit")
                            except (TypeError, ValueError, KeyError):
                                pass
                            else:
                                _pb = min(_pb, total_value, settings.max_notional_per_member)
                                _pcap = m.get("daily_cap_usd")
                                if _pcap is not None and _pcap > 0:
                                    _pb = min(_pb, _pcap)
                                budget = round(max(0.0, _pb), 2)
                                stamped_stake = _ps
                                commit_target = float(_pc) if _pc is not None else None
                                _reversed = 0.0
                                if will_place:
                                    with db() as conn:
                                        # ONE transaction: the new stamp, the
                                        # drain move, the old stamp's retirement
                                        # and the supersede below land together
                                        # or not at all. Five autocommit writes
                                        # left a kill between them with the new
                                        # stamp in place and the old rows still
                                        # creditable — the same dollars twice.
                                        conn.execute("BEGIN IMMEDIATE")
                                        try:
                                            conn.execute(
                                                "INSERT INTO kv(key,value) VALUES(?,?) "
                                                "ON CONFLICT(key) DO UPDATE SET "
                                                "value=excluded.value",
                                                (budget_stamp, json.dumps(
                                                    {"budget": budget, "stake": stamped_stake,
                                                     "commit": commit_target})))
                                            # The adopted budget was sized before
                                            # any credit that cycle drained, so the
                                            # drain must ride forward with it or the
                                            # write-back overwrites those dollars
                                            # out of existence.
                                            _pdk = f"carry_drained:{_prev['id']}:{mid}"
                                            _pd = (conn.execute(
                                                "SELECT value FROM kv WHERE key=?",
                                                (_pdk,)).fetchone() or {"value": None})["value"]
                                            if _pd:
                                                conn.execute(
                                                    "INSERT INTO kv(key,value) VALUES(?,?) "
                                                    "ON CONFLICT(key) DO UPDATE SET "
                                                    "value=excluded.value",
                                                    (f"carry_drained:{sid}:{mid}", _pd))
                                                conn.execute("DELETE FROM kv WHERE key=?", (_pdk,))
                                            conn.execute("DELETE FROM kv WHERE key=?",
                                                         (_prev_key,))
                                            # This budget re-deploys what the
                                            # interrupted cycle could not, so its
                                            # errored buys must not ALSO be credited
                                            # back to the slice — and where
                                            # reconcile already HAS credited one
                                            # (its age-out and this adoption
                                            # window overlap for days 6-10 of a
                                            # weekly cadence, and the old UPDATE
                                            # here matched 0 rows once the row
                                            # read 'skipped'), take the credit
                                            # back before the write-back drains
                                            # it on top of the adopted budget.
                                            for _r in conn.execute(
                                                    "SELECT id, status, place_json FROM executions "
                                                    "WHERE session_id=? AND member_id=? AND side='buy' "
                                                    "AND status IN ('error','skipped')",
                                                    (_prev["id"], mid)).fetchall():
                                                try:
                                                    _pj = json.loads(_r["place_json"] or "{}")
                                                except (TypeError, ValueError):
                                                    _pj = {}
                                                _cr = _num(_pj.get("_concord_carry_credited"))
                                                _claim = _pj.get("_concord_awaiting_credit")
                                                if _r["status"] == "skipped" and not _cr and _claim is None:
                                                    continue        # an ordinary deferral — not ours
                                                if _cr:
                                                    _reversed += _cr
                                                    _pj["_concord_credit_reversed"] = _cr
                                                    _pj.pop("_concord_carry_credited", None)
                                                _pj.pop("_concord_awaiting_credit", None)
                                                conn.execute(
                                                    "UPDATE executions SET status='superseded', "
                                                    "place_json=? WHERE id=?",
                                                    (json.dumps(_pj), _r["id"]))
                                            if _reversed:
                                                conn.execute(
                                                    "UPDATE member_settings SET carry_pending="
                                                    "MAX(0, COALESCE(carry_pending,0)-?) "
                                                    "WHERE member_id=?", (round(_reversed, 2), mid))
                                            conn.execute("COMMIT")
                                        except BaseException:
                                            conn.execute("ROLLBACK")
                                            raise
                                    audit("system", "budget_carried_forward", str(sid),
                                          {"member_id": mid, "from_session": _prev["id"],
                                           "budget": budget, "credit_reversed": round(_reversed, 2)})
                if budget is None:
                    # Mark plausibility gate. The ride below anchors the
                    # COMMITMENT to club_value and the write-back persists
                    # whatever it computes — so a mark that is wrong (a feed
                    # that stopped at page one, a positions read missing
                    # names, proceeds parked as cash the mark cannot see)
                    # becomes the member's new stake, permanently: 2026-09-24
                    # took a $990 commitment to $82 in four cycles without a
                    # single error. A real market loss shows in the account
                    # total as well; a slice that shrank while the account
                    # did not is dollars the club lost SIGHT of, not dollars
                    # the member lost. Fail closed and say so.
                    _honored = _num(m.get("alloc_applied"))
                    if _honored is not None and _honored >= _MARK_GATE_MIN_USD:
                        # The mark is every dollar the club can ACCOUNT for:
                        # positions, this cycle's carry, credits waiting to be
                        # drained, and buys that errored but are not yet
                        # proven absent (A4/reconcile resolve those — either
                        # into positions or into carry). Only dollars none of
                        # these explain are "missing".
                        with db() as conn:
                            _acct = conn.execute(
                                "SELECT COALESCE((SELECT carry_pending FROM member_settings "
                                "WHERE member_id=?),0) AS pending, "
                                "COALESCE((SELECT SUM(dollar_amount) FROM executions "
                                "WHERE member_id=? AND side='buy' AND status='error'),0) AS err",
                                (mid, mid)).fetchone()
                        _mark = (club_value
                                 + max(0.0, _num(m.get("slice_carry")) or 0.0)
                                 + max(0.0, _num(_acct["pending"]) or 0.0)
                                 + max(0.0, _num(_acct["err"]) or 0.0))
                        _missing = _honored - _mark
                        if (_missing > max(_MARK_DROP_FRAC * _honored, _MARK_GATE_MIN_USD)
                                and total_value >= (1.0 - _MARK_DROP_FRAC) * _honored):
                            _akey = f"mark_accept:{mid}"
                            _detail = {"member_id": mid, "honored": round(_honored, 2),
                                       "mark": round(_mark, 2),
                                       "total_value": round(total_value, 2)}
                            if kv_get(_akey) == "1":
                                # An admin looked and confirmed the slice really
                                # is smaller (the member sold club shares by
                                # hand, say). Honor it once, on the live pass.
                                if will_place:
                                    kv_set(_akey, "0")
                                audit("system", "slice_mark_accepted", str(sid), _detail)
                            else:
                                audit("system", "slice_mark_suspect", str(sid), _detail)
                                _skip_member(
                                    msum, "slice_mark_suspect",
                                    f"club slice marks ${_mark:.2f} against ${_honored:.2f} "
                                    f"deployed while the account is worth ${total_value:.2f} — "
                                    "positions or cash the club cannot see; skipping this run "
                                    f"(admin: kv mark_accept:{mid}=1 honors the smaller slice)")
                                continue
                    # The committed figure in DOLLARS. A percent stake is a way of
                    # SPECIFYING the amount; once deployed it compounds like any
                    # other, so the percentage is resolved against the account only
                    # while the slice is still empty.
                    stake_usd = float(m["alloc_value"] or 0.0)
                    carry = m.get("slice_carry")
                    ride = alloc.compound_budget(stake_usd, m.get("alloc_applied"),
                                                 club_value, total_value,
                                                 daily_cap_usd=None, slice_carry=carry)
                    # Deployable capacity = the marked positions plus spendable
                    # cash — a commitment above it can never fully deploy and
                    # would defer/carry the excess forever. Carry is NOT added
                    # here: carry is dollars we declined to spend, which are
                    # only real if they are still in the account as cash, and
                    # cash is what buying_power reports. Counting carry on both
                    # sides made ride <= capacity an identity, so a carry whose
                    # dollars had left the account could never mature the clamp.
                    capacity = club_value + deployable(buying_power)
                    eff = min(ride, capacity)
                    cap = m.get("daily_cap_usd")
                    budget = round(max(0.0, min(eff, cap)
                                       if (cap is not None and cap > 0) else eff), 2)
                    commit_target = _mature_clamp(msum, mid, ride, eff,
                                                  max(5.0, 0.02 * stake_usd))
                    # Stamp the stake the budget EMBODIES alongside it: the buy-day
                    # write-back must credit that figure, not whatever the member
                    # has since changed it to (a non-voting member can edit their
                    # stake mid-cycle — the vote-based freeze doesn't cover them).
                    stamped_stake = float(stake_usd or 0.0)
                    # Stamp only live-armed runs: dry/blocked runs recompute
                    # fresh each time and must leave no live-sizing side effects.
                    if will_place:
                        kv_set(budget_stamp, json.dumps({"budget": round(budget, 2),
                                                         "stake": stamped_stake,
                                                         "commit": commit_target}))
        else:
            ride = alloc.rebalance_budget(m["alloc_type"], m["alloc_value"], total_value,
                                          buying_power, daily_cap_usd=None)
            capacity = club_value + deployable(buying_power)
            eff = min(ride, capacity)
            cap = m.get("daily_cap_usd")
            budget = round(max(0.0, min(eff, cap)
                               if (cap is not None and cap > 0) else eff), 2)
            # Only a COMPOUNDING member's commitment is ever rewritten (the
            # write-back is compounding-gated), so only they can mature a
            # clamp — announcing a write-down to a pinned member would promise
            # an adjustment that never happens. Their budget is still capped to
            # capacity above; it simply doesn't touch the committed figure.
            commit_target = (_mature_clamp(msum, mid, ride, eff, max(5.0, 0.02 * ride))
                             if compounding else None)
            # The resolved dollars are what a compounding member is converted to
            # once this cycle deploys.
            stamped_stake = float(budget)

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
        if n_eff < len(picks):
            # Rank hysteresis: daily re-ranking crossing the n_eff boundary
            # forced full sell/re-buy round trips of still-in-basket names
            # almost every cycle for a small stake. A held name keeps its slot
            # while it stays within n_eff+H club-wide; free slots fill by rank.
            _H = 4        # rank band a held name may slip within before rotating out
            _band = picks[:min(len(picks), n_eff + _H)]
            keepers = [s for s in _band
                       if min(held.get(s, 0.0), live.get(s, 0.0)) > 0][:n_eff]
            fills = [s for s in picks if s not in keepers][:max(0, n_eff - len(keepers))]
            _rank = {s: i for i, s in enumerate(picks)}
            basket = sorted(keepers + fills, key=_rank.__getitem__)
        else:
            basket = picks[:n_eff]
        per_pick = math.floor((budget / n_eff) * 100) / 100.0
        want = set(basket)
        # Per-name dollar targets that SUM to the budget (water-fill): a name
        # within $1 of fair share keeps its value and the others absorb the
        # difference — winners ride, entries shrink, the total adds up. A name
        # >$1 over fair share is targeted at fair share (the sell leg trims it).
        # Club-capped, exactly like the valuation and the sell cap: a personal
        # position in a basket name must not inflate that name's target (which
        # would subtract personal dollars from every other name's share).
        _held_val_of = (lambda sym: min(held.get(sym, 0.0),
                                        live.get(sym, 0.0) if settings.buy_sizing_account
                                        else held.get(sym, 0.0)) * (prices.get(sym) or 0.0))
        targets = alloc.basket_targets(budget, {sym: _held_val_of(sym) for sym in basket})
        msum["buying_power"] = round(buying_power, 2)
        msum["allocation"] = round(budget, 2)
        msum["budget"] = round(budget, 2)
        msum["per_pick"] = per_pick
        msum["compounding"] = compounding
        order_count = 0
        unplaced_usd = 0.0   # club dollars this leg meant to deploy but could not
        resized: set[str] = set()   # names exited purely to re-enter at target
        # Per-member order ceiling = the tighter of the global cap and this
        # member's own max_orders (finding #35 — the column was fetched but the
        # global was always used).
        member_max_orders = min(settings.max_orders, int(m.get("max_orders") or settings.max_orders))
        # SELLS DO NOT SPEND THE BUY BUDGET.
        #
        # One shared counter meant a full rotation starved its own buy leg: a
        # cycle can need up to 2 x TOP_N orders (exit every old name, enter every
        # new one), so with TOP_N=20 and MAX_ORDERS=25 the sells consumed the
        # budget and the last buys were dropped as "max_orders cap". Observed
        # 2026-08-31: a member needing 35 orders placed 25 and ended the cycle
        # holding 16 of 20 basket names, with the undeployed dollars silently
        # left in cash — the club had committed more than it managed to hold.
        #
        # The cap exists to bound how many orders one member can generate in a
        # cycle, and it still does: each LEG is capped independently. Sells are
        # additionally self-limiting (you cannot sell more names than you hold),
        # and starving them is worse than starving buys — an unsold exit leaves
        # the member holding a name the club voted OUT of the basket.
        sell_count = 0

        # ── SELL phase: exit dropped names + trim overweight basket names ──
        # Sells come ONLY from `held` (club-bought, reconstructed from executions),
        # so a member's own non-club positions are never touched. A name that left
        # the basket is fully sold; a name still in the basket but OVER its equal-
        # weight target is trimmed back to target, so the proceeds redistribute
        # into the rest of the new basket.
        placed_sell_usd = 0.0   # ≈ proceeds in flight; the both-day BP poll's early-exit target
        # Club value the sell leg meant to release but could not (a blocked
        # pre-trade alert, a review failure, the order cap, the PDT guard). The
        # budget was sized WITH it, so the buy leg must deploy that much less
        # this cycle — or it buys the shortfall out of the member's own idle
        # cash — and it must not be carried, because the dollars never left the
        # positions the next mark will count again.
        unsold_club_usd = 0.0
        unpriced_sell_placed = False   # a placed sell with no quote → its proceeds are untracked
        # PDT guard, keyed on TIME, not session: anything this member BOUGHT
        # since the previous trading day's close (16:00 ET) must not be sold
        # today — a same-day round trip is a FINRA day trade. Covers same-day
        # re-runs selling the morning's buys, evening admin repairs filling at
        # the open, and DUSTFIX crumb repairs (they are placed buy rows) —
        # across ALL sessions. 'error' buys included conservatively (the
        # broker may have accepted them).
        from ..calendar_us import at_et, prev_trading_day
        _pdt_cutoff = int(at_et(prev_trading_day(_today_et()), "16:00").timestamp())
        with db() as conn:
            bought_today = {r["symbol"] for r in conn.execute(
                "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND side='buy' "
                "AND status IN ('placed','error') AND created_at >= ?",
                (mid, _pdt_cutoff)).fetchall()}
        # One member-scoped token refresh: a pass can outlive the access token
        # (throttle retries + the 120s BP poll), and a mid-pass expiry must not
        # spray per-order error rows. `op` re-reads `client`, so the single
        # retry runs against the refreshed one with the same ref_id.
        auth_retried = False
        auth_stranded = False

        def _broker(op):
            nonlocal auth_retried, client
            try:
                return op()
            except RHAuthError:
                if auth_retried:
                    raise
                auth_retried = True
                client = vault.ensure_client(mid)   # re-grab refreshes an expired token
                return op()
        for symbol, qty in (sorted(held.items()) if phase in ("sell", "both") else []):
            if auth_stranded:
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                       "note": "auth expired mid-pass"})
                continue
            if symbol in bought_today:
                if symbol not in want:          # a deferred EXIT keeps its value in the slice
                    unsold_club_usd += min(qty, live.get(symbol, 0.0)) * (prices.get(symbol) or 0.0)
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                       "note": "bought today — PDT guard, exits next session"})
                continue
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
                    # can't size a trim without a price — on the record, so a
                    # dead quote on an in-basket name is not silent
                    _record(sid, mid, symbol, None, ref_id_for(sid, mid, f"SELL:{symbol}"),
                            "skipped", error="no quote for trim sizing", side="sell")
                    msum["orders"].append({"symbol": symbol, "side": "sell",
                                           "status": "skipped", "note": "no quote for trim sizing"})
                    continue
                _tgt = targets.get(symbol, per_pick)
                excess = held_qty - (_tgt / price)    # shares above this name's water-fill target
                short = -excess * price               # $ BELOW target (negative excess)
                if excess * price >= 1.00:
                    sell_qty, why = excess, "trim to target"
                elif short >= _RESIZE_MIN_FRAC * _tgt and short < 1.00:
                    # Materially under target, but the top-up ($<1) is smaller
                    # than Robinhood's minimum order — so it can never be closed
                    # by buying more. Exit the position fully and let the buy leg
                    # re-enter at the correct size (owner call 2026-08-24). This
                    # is the only way a small stake can rebalance at all: with a
                    # ~$1.30 per-name target, almost every drift is unplaceable.
                    # Also absorbs in-basket positions drifted below $1 —
                    # previously unreachable by any convergence path;
                    # basket_targets guarantees the re-entry is ≥ $1.
                    sell_qty, why = held_qty, "resize · rebuy at target"
                    resized.add(symbol)
                else:
                    continue                          # at target, or drift too small to churn
            else:
                sell_qty, why = held_qty, "exited basket"  # full exit of a dropped name
            if (mid, symbol, "sell") in already:
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "placed",
                                       "note": "already sold"})
                continue
            if sell_count >= member_max_orders:
                unsold_club_usd += sell_qty * (price or 0.0)
                # The sell leg has its own budget (see member_max_orders above),
                # so a rotation can never starve its own buys — but it is still
                # bounded, so one member cannot generate unlimited orders.
                _record(sid, mid, symbol, None, ref_id_for(sid, mid, f"SELL:{symbol}"),
                        "skipped", error="max_orders cap reached (sell leg)", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell",
                                       "status": "skipped", "note": "max_orders cap · sell leg"})
                continue
            ref_id = ref_id_for(sid, mid, f"SELL:{symbol}")
            sell_qty = min(sell_qty, live.get(symbol, 0.0))   # cap to shares actually held
            # A7: on a FULL exit, sweep a DUST residual up to the live quantity so
            # the position closes cleanly — but ONLY at drift scale: est-vs-fill
            # residue is a fraction of a percent of the position, so the 2%
            # relative cap keeps every legitimate sweep while a member's own
            # sub-$1 personal stake (large relative to a ~$1.30 club slice)
            # stays untouched.
            sweep = False
            if why == "exited basket":
                live_qty = live.get(symbol, 0.0)
                residual = live_qty - sell_qty
                if residual > 0 and (residual < MIN_SELL_QTY
                                     or (price is not None and residual * price <= 1.00
                                         and residual <= 0.02 * sell_qty)):
                    sell_qty, sweep = live_qty, True
                    why = "exited basket · dust swept"
            # Robinhood REJECTS a fractional order quantity that carries any non-zero
            # digit beyond the 6th decimal ("Order quantity cannot include fractional
            # shares"), so EVERY sell floors to 6dp — via floor_qty6, whose epsilon
            # keeps a full-position 0.00399 from becoming 0.003989 (the float bug
            # that minted Charlie's unsellable ALL microshare). The old 8dp
            # full-exit (meant to avoid leaving a ~1e-6 crumb) instead
            # hard-rejected the ENTIRE order for any holding whose live balance
            # had >6 decimals — stranding the whole position on every cycle
            # (AOS/ALLE/BLDR).
            qfloor = floor_qty6(sell_qty)
            if qfloor <= 0:
                continue
            if qfloor < MIN_SELL_QTY:
                # an unsellable residue: the broker rejects it, the member gets
                # the notification, and the value at stake is sub-cent — leave
                # it in place, on the record
                _record(sid, mid, symbol, None, ref_id, "skipped", side="sell",
                        error="residue below broker sell minimum — left in place")
                msum["orders"].append({"symbol": symbol, "side": "sell",
                                       "status": "skipped", "note": "unsellable residue"})
                continue
            qstr = f"{qfloor:.6f}"
            try:
                review = _broker(lambda: client.review_market_sell(m["account_number"], symbol, qstr))
            except RHAuthError as e:
                # token refresh + retry both failed — strand the member's pass
                unsold_club_usd += qfloor * (price or 0.0)
                _record(sid, mid, symbol, None, ref_id, "skipped",
                        error=f"sell review failed: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                       "note": "auth expired mid-pass"})
                msum["note"] = "auth expired mid-pass — remaining legs deferred"
                # Deliberately NOT a member skip_reason: that gates off the
                # write-back, and the buy leg below still accrues every
                # un-bought name into slice_carry. Discarding it would lose the
                # sold proceeds from the slice for good. Only a pass that
                # already PLACED something leaves the cycle unfinished, so only
                # that holds the executed flip.
                if sell_count:
                    msum["auth_stranded"] = True
                    _hold_strand(sid, mid, will_place)
                auth_stranded = True
                continue
            except Exception as e:  # noqa: BLE001
                # 'skipped', not 'error': nothing reached the broker, so there
                # is no fill to recover and no reason to hold the session's
                # executed-flip open — the name is retried next session.
                unsold_club_usd += qfloor * (price or 0.0)
                _record(sid, mid, symbol, None, ref_id, "skipped",
                        error=f"sell review failed: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped", "note": str(e)})
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                unsold_club_usd += qfloor * (price or 0.0)
                _record(sid, mid, symbol, None, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped", "alerts": alerts})
                continue
            if not will_place:
                _record(sid, mid, symbol, None, ref_id, "reviewed", review=review, side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "reviewed", "qty": qstr, "note": why})
                if symbol in resized:
                    # mirror the placed branch: without this a dry-run preview
                    # shows "sell reviewed + buy at-target" where a live run
                    # would sell AND rebuy.
                    live[symbol] = 0.0
                    held[symbol] = 0.0
                continue
            try:
                # NOTE: the prior errored sell row is deliberately left standing.
                # It may represent a PARTIAL fill, and it is the only record of
                # those shares; reconcile resolves it against the broker (an
                # order-id bind, or a same-date 1:1 match that 'placed' rows
                # claim first), which is what keeps it off this new order.
                place = _broker(lambda: client.place_market_sell(m["account_number"], symbol, qstr, ref_id=ref_id))
                if isinstance(place, dict):
                    place["_concord_est_qty"] = qfloor   # shares sold (broker fill is async)
                _record(sid, mid, symbol, None, ref_id, "placed", review=review, place=place, side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "placed", "qty": qstr, "note": why})
                if symbol in resized:
                    # `live`/`held` were read BEFORE this sell; without clearing
                    # them the buy leg would still size against the old holding
                    # and skip the name for the same sub-$1 reason.
                    live[symbol] = 0.0
                    held[symbol] = 0.0
                sell_count += 1
                placed_sell_usd += (price or 0.0) * qfloor
                if price is None:
                    unpriced_sell_placed = True   # proceeds pending but untracked
            except RHAuthError as e:
                # place-stage: the broker may have accepted it — keep 'error'
                # so A4/reconcile can verify, then strand the rest of the pass.
                _record(sid, mid, symbol, None, ref_id, "error", review=review, error=f"sell: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "error", "note": str(e)})
                msum["note"] = "auth expired mid-pass — remaining legs deferred"
                msum["auth_stranded"] = True     # this sell reached the broker
                _hold_strand(sid, mid, will_place)
                auth_stranded = True
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, None, ref_id, "error", review=review, error=f"sell: {e}", side="sell")
                msum["orders"].append({"symbol": symbol, "side": "sell", "status": "error", "note": str(e)})

        # Same-day ('both') runs: the portfolio snapshot above predates this
        # morning's sells, so its buying_power cannot see their proceeds — on
        # the first live both-day (2026-08-19) every buy deferred with
        # "beyond settled buying power" even for limited-margin members, whose
        # proceeds ARE spendable immediately. The first fix re-fetched once
        # after ~4s and STILL deferred everything on 08/19's second both-day:
        # Robinhood credits buying power only after the market sells FILL and
        # its ledger catches up, which takes well over 8 seconds at 9:31. So:
        # poll buying power for up to BP_POLL_MAX_S, exiting early once ~98%
        # of the placed sells' value shows up (or once the number rises and
        # stops rising — credit landed). Cash accounts report settled-only
        # buying power that never moves same-day, so they keep a single quick
        # look. A fetch error is a flat poll, not an abort (one transient 5xx
        # must not defer the whole buy leg a day); ~5 consecutive failures is
        # a hard outage and gives up. EVERY good reading is adopted — lower
        # too: the last broker figure is the truth, and a stale high number
        # would size buys beyond real BP.
        #
        # Hoisted out of the poll below: the BUY loop also needs to know whether
        # this account settles instantly, and it used to be assigned only when a
        # sell had been placed.
        acct_type = (kv_get(f"acct_type:{mid}") or "").strip().lower()
        settles_instantly = acct_type != "cash"
        if phase == "both" and sell_count > 0 and not auth_stranded:
            deadline_s = 0 if settles_instantly is False else BP_POLL_MAX_S
            bp_start = buying_power
            target = bp_start + BP_POLL_TARGET_FRAC * placed_sell_usd
            waited = 0
            flat = 0
            fetch_errs = 0
            while True:
                try:
                    port2 = client.get_portfolio(m["account_number"])
                    p2 = port2.get("data", port2) if isinstance(port2, dict) else {}
                    b2 = p2.get("buying_power") or {}
                    bp_new = _num(b2.get("buying_power") if isinstance(b2, dict) else b2)
                    fetch_errs = 0
                except Exception:  # noqa: BLE001
                    bp_new = None
                    fetch_errs += 1
                    if fetch_errs >= 5:
                        break
                if bp_new is not None:
                    rose = bp_new > buying_power
                    buying_power = bp_new
                    flat = 0 if rose else flat + 1
                else:
                    flat += 1
                # An unpriced placed sell has proceeds the target can't count —
                # rely on the plateau/deadline alone in that case.
                if (placed_sell_usd > 0 and not unpriced_sell_placed
                        and buying_power >= target):
                    break                    # proceeds essentially all credited
                if buying_power > bp_start and flat >= BP_POLL_FLAT_POLLS:
                    break                    # credit landed and has stopped growing
                if waited >= deadline_s:
                    break
                time.sleep(BP_POLL_EVERY_S)
                waited += BP_POLL_EVERY_S

        # Reserve funding for this member's crumb repairs BEFORE the buy leg
        # can spend buying power to ~$0: the sweep runs post-buy-leg (fills
        # mint crumbs during the day), so its budget must be reserved, not
        # left over — or the repair provably never fires for exactly the
        # fully-deployed accounts crumbs accumulate on.
        repair_reserve = 0.0
        if phase in ("buy", "both"):
            with db() as conn:
                _club_syms = {r["symbol"] for r in conn.execute(
                    "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND status='placed'",
                    (mid,)).fetchall()}
            _crumbs = [s for s, q in live.items()
                       if s in _club_syms and s not in want and q < 0.01
                       and 0 < floor_qty6(q) < MIN_SELL_QTY]
            if _crumbs:
                repair_reserve = (min(len(_crumbs), _REPAIR_MAX_PER_RUN) * _REPAIR_BUY_USD
                                  + (_REPAIR_MIN_BP - _REPAIR_BUY_USD))
        # Only deploy what's actually SETTLED-spendable — any buy beyond buying
        # power is deferred (it fills next buy day, once this cycle's sells
        # settle), so we never trip EQUITY_NOT_ENOUGH_BP. Hold back the small
        # cash reserve (finding #28 — deployable() was defined but never used)
        # so a sequence of dollar market orders doesn't exhaust buying power on
        # the last leg.
        bp_left = max(0.0, deployable(buying_power) - repair_reserve)
        if unsold_club_usd > 0:
            # The names above are still in the slice, so their value is not
            # ours to redeploy this cycle: shrink the leg's headroom by it. The
            # shortfall defers ("tops up next cycle") and is netted out of the
            # carry at the write-back — it lands when the sell finally does.
            bp_left = max(0.0, bp_left - unsold_club_usd)
            msum["unsold_club_usd"] = round(unsold_club_usd, 2)
        # A4: lazy one-shot fetch of the member's recent broker orders, only when a
        # prior run left an errored buy whose true fate is unknown.
        recent_orders: list[dict] | None = None
        orders_exhausted = False        # did the broker hand us the WHOLE feed?
        orders_fetched = False
        # ── BUY phase: top each basket name up to its equal-weight target ──
        # need = target − current club value, so names already held aren't re-bought
        # and the proceeds from the sells above fund the newly-added names.
        for symbol in (basket if phase in ("buy", "both") else []):
            if auth_stranded:
                # The pass is over, but the dollars are not gone: carry every
                # name the leg never reached, or a mid-pass token expiry would
                # silently shrink a compounding slice by the undeployed rest.
                # Two names are NOT ours to carry — one already placed this
                # session, and one whose errored row is still unresolved, whose
                # dollars reconcile credits once it can prove their fate.
                if (mid, symbol, "buy") in already:
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed",
                                           "note": "already placed"})
                    continue
                if (mid, symbol) in prior_error_buys:
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                           "note": "unresolved prior error — left for reconcile"})
                    continue
                _hv = (live.get(symbol, 0.0) if settings.buy_sizing_account
                       else held.get(symbol, 0.0)) * (prices.get(symbol) or 0.0)
                unplaced_usd += max(0.0, targets.get(symbol, per_pick) - _hv)
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                       "note": "auth expired mid-pass · tops up next cycle"})
                continue
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
                        recent_orders, orders_exhausted = _fetch_recent_orders(
                            client, m["account_number"])
                    except Exception:  # noqa: BLE001
                        recent_orders = None
                if recent_orders is None:
                    # Leave the surviving error row alone — no shadow row. It
                    # retries next run, and its original created_at keeps the
                    # match window honest.
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
                    # Promote the ORIGINAL row in place rather than superseding
                    # it and inserting a new one: its created_at is when the
                    # order actually went to the broker, which is what the PDT
                    # guard and reconcile's date-bound matching key off.
                    place = {"_concord_recovered": True, "data": {"order": matched}}
                    px, amt = prices.get(symbol), prior.get("dollar_amount")
                    if px and amt:
                        place["_concord_est_qty"] = round(amt / px, 6)  # est shares (async fill)
                    with db() as conn:
                        conn.execute(
                            "UPDATE executions SET status='placed', place_json=? "
                            "WHERE id=? AND status='error'",
                            (json.dumps(place), prior["id"]))
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed",
                                           "note": "recovered — prior errored place found at broker"})
                    order_count += 1
                    continue
                if not _feed_covers(recent_orders, orders_exhausted,
                                    prior.get("created_at")):
                    # Not found, but the feed never reached back to the moment we
                    # tried to place it — so this is "not found YET", not proof.
                    # Re-placing on that would be the blind re-place this whole
                    # path exists to prevent, and it buys real money twice. Treat
                    # it exactly as an unreadable feed: leave the error row
                    # standing as evidence and let reconcile resolve it, which is
                    # also what returns the dollars (its age-out credits
                    # carry_pending once absence IS provable — so nothing is
                    # accrued here, or the same dollars would come back twice).
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                           "note": "prior error unproven — broker feed did not "
                                                   "reach back; retries next run"})
                    continue
                # Verified ABSENT at the broker — the prior place never landed.
                # Retire the row here, on the proof itself: from this point the
                # dollars belong to this run (it either re-places them or
                # carries them into slice_carry), so a row left standing would
                # let reconcile credit the same dollars a second time.
                if will_place:
                    _supersede_error(sid, mid, symbol, "buy")
            price = prices.get(symbol)
            if price is None:
                # can't value an existing holding or estimate filled shares → skip,
                # so we never blindly re-buy a name we may already hold.
                _record(sid, mid, symbol, None, ref_id_for(sid, mid, symbol), "skipped",
                        error="no quote for sizing")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": "no quote"})
                # Carry the undeployed target so the dollars retry next cycle —
                # but only when nothing of the name is held: a held name with no
                # quote accrues 0 (over-crediting risk; the hoisted valuation
                # already fail-closes most of these).
                held_any = (live.get(symbol, 0.0) if settings.buy_sizing_account
                            else held.get(symbol, 0.0)) > 0
                if not held_any:
                    unplaced_usd += targets.get(symbol, per_pick)
                continue
            # How much of this name is ALREADY held — read from the live account
            # (buy_sizing_account) so a drifted/stale ledger or a manual sale can't
            # mis-size the top-up; fall back to the club ledger only if disabled.
            held_now = live.get(symbol, 0.0) if settings.buy_sizing_account else held.get(symbol, 0.0)
            held_val = held_now * price
            _tgt = targets.get(symbol, per_pick)
            need = round(_tgt - held_val, 2)
            partial_of = None      # set only when this name is bought short
            if need < 1.00:
                # Two different reasons, and they must not read the same: either
                # the name really is at/over target, or the top-up is real but
                # smaller than Robinhood's $1 minimum order (common on small
                # stakes, where a per-name target is barely over $1 itself).
                #
                # THE SECOND CASE IS UNDEPLOYED CLUB MONEY AND MUST CARRY.
                # A name short of target has three fates: >= $1 the buy leg tops
                # it up; >= _RESIZE_MIN_FRAC of target the sell leg exits it
                # whole and the buy leg re-enters at size; below BOTH it is
                # unreachable this cycle. Returning here without accruing left
                # those dollars nowhere — not deployed, not resized, not carried
                # — so the positions-only mark lost them and the write-back
                # stamped the shrunken figure as the new committed stake. Live
                # 2026-09-15: a $20.93 stake ratcheted to $13.49 over eleven
                # sessions, ~$7 stranded as idle cash, nobody having touched it.
                # It bites hardest where per-name targets sit near $1 — the
                # smallest stakes.
                #
                # need <= 0 carries NOTHING: the name is at or above target and
                # owes the slice nothing. Carrying there would top the slice up
                # out of the member's own cash on any down move, which is the
                # one thing compounding must never do.
                if need > 0:
                    unplaced_usd += need
                note = (f"at target (${held_val:.2f} ≥ ${_tgt:.2f})" if need <= 0 else
                        f"top-up ${need:.2f} below the $1 minimum order "
                        f"(${held_val:.2f} of ${_tgt:.2f})")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "held",
                                       "note": note})
                continue
            if order_count >= member_max_orders:
                # Cap check BEFORE affordability: a capped member places
                # nothing regardless, so a capped name must neither consume
                # buy-leg headroom nor be mislabeled a partial.
                unplaced_usd += need
                _record(sid, mid, symbol, need, ref_id_for(sid, mid, symbol), "skipped",
                        error="max_orders cap reached")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": "max_orders cap"})
                continue
            if need > bp_left + 0.005:
                # BUY WHAT IS AFFORDABLE rather than dropping the name.
                #
                # This branch used to skip the whole position, which starved
                # whichever name the loop reached last — always the same one,
                # since the basket order is stable. Observed 2026-09-03: ADM
                # missed on two accounts, and one of them was short by $0.02 of
                # $5.45. Holding a name 0.4% light is strictly better than not
                # holding it, and the next cycle tops it up.
                #
                # Floor to cents so the order can never exceed what is actually
                # there, and only if the remainder clears Robinhood's $1
                # minimum — below that no order is placeable at all.
                #
                # The settles_instantly gate is DELIBERATE: a cash account's
                # reported buying power may include unsettled proceeds, and a
                # partial bought with them risks a good-faith violation —
                # whole-name deferral is the conservative choice (see
                # test_both_day_cash_account_keeps_single_quick_look).
                afford = math.floor(bp_left * 100) / 100.0
                if settles_instantly and afford >= 1.00:
                    msum.setdefault("partials", []).append(
                        {"symbol": symbol, "wanted": round(need, 2), "bought": afford})
                    partial_of = need          # what the full top-up would have been
                    unplaced_usd += need - afford   # the remainder still owes; carries
                    need = afford
                else:
                    unplaced_usd += need
                    # "settles next cycle" was wrong on a limited-margin account:
                    # it has no settlement wait, the proceeds simply had not been
                    # credited yet. Say what actually happened.
                    _record(sid, mid, symbol, need, ref_id_for(sid, mid, symbol), "skipped",
                            error=f"short ${need - bp_left:.2f} — buying power not credited yet")
                    msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                           "note": f"short ${need - bp_left:.2f} · tops up next cycle"})
                    continue
            ref_id = ref_id_for(sid, mid, symbol)
            amt = f"{need:.2f}"
            try:
                review = _broker(lambda: client.review_market_buy(m["account_number"], symbol, amt))
            except RHAuthError as e:
                # Nothing reached the broker — carry the dollars and strand the
                # rest of the pass. Deliberately NOT a member skip_reason: this
                # leg already placed orders, and the write-back is what records
                # the carry that funds the un-bought names next cycle. The
                # member is stranded at the TOP of the next run instead (vault's
                # refresh failure already marked the connection).
                unplaced_usd += need
                _record(sid, mid, symbol, need, ref_id, "skipped", error=f"review failed: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped",
                                       "note": "auth expired mid-pass · tops up next cycle"})
                if sell_count or order_count:
                    msum["auth_stranded"] = True
                    _hold_strand(sid, mid, will_place)
                auth_stranded = True
                continue
            except Exception as e:  # noqa: BLE001
                # 'skipped', not 'error': nothing reached the broker, so the
                # dollars retry next cycle via slice_carry instead of holding
                # the session's executed-flip open.
                unplaced_usd += need
                _record(sid, mid, symbol, need, ref_id, "skipped", error=f"review failed: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "note": str(e)})
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                unplaced_usd += need   # nothing placed — the dollars carry
                _record(sid, mid, symbol, need, ref_id, "skipped", review=review,
                        error="blocking pre-trade alert")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "skipped", "alerts": alerts})
                continue
            # Consume headroom only once the order is reviewed clean and
            # unblocked — cap/blocked/review-failure paths above no longer
            # deplete it. NO refund on a place exception (the broker may have
            # accepted the order; A4 verifies).
            bp_left -= need
            if not will_place:
                _record(sid, mid, symbol, need, ref_id, "reviewed", review=review)
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "reviewed", "amount": amt,
                                       **({"note": f"partial · ${partial_of:.2f} wanted"} if partial_of else {})})
                continue
            try:
                place = _broker(lambda: client.place_market_buy(m["account_number"], symbol, amt, ref_id=ref_id))
                if isinstance(place, dict):
                    place["_concord_est_qty"] = round(need / price, 6)  # est shares (async fill)
                _record(sid, mid, symbol, need, ref_id, "placed", review=review, place=place)
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "placed", "amount": amt,
                                       **({"note": f"partial · ${partial_of:.2f} wanted"} if partial_of else {})})
                order_count += 1
            except RHAuthError as e:
                # place-stage: 'error' stays (the broker may have accepted it —
                # A4 verifies; no carry accrual for the same reason). Not a
                # member skip: the write-back must still record the carry the
                # stranded remainder of the leg accrues.
                _record(sid, mid, symbol, need, ref_id, "error", review=review, error=f"place: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "error", "note": str(e)})
                msum["auth_stranded"] = True
                auth_stranded = True
            except Exception as e:  # noqa: BLE001
                _record(sid, mid, symbol, need, ref_id, "error", review=review, error=f"place: {e}")
                msum["orders"].append({"symbol": symbol, "side": "buy", "status": "error", "note": str(e)})
        # A4 leftovers: verify this member's NON-BASKET errored buys (DUSTFIX
        # repairs, n_eff-dropped names). No plan row exists to re-place them,
        # so the row is promoted in place when the broker shows the order, or
        # retired 'superseded' when verified absent — never re-placed.
        for (pmid, psym), prior in (list(prior_error_buys.items()) if will_place else []):
            if pmid != mid or psym in set(basket):
                continue
            if not orders_fetched:
                orders_fetched = True
                try:
                    recent_orders, orders_exhausted = _fetch_recent_orders(
                        client, m["account_number"])
                except Exception:  # noqa: BLE001
                    recent_orders = None
            if recent_orders is None:
                break               # unverifiable — leave the rows for the next run
            _w_start = session.get("opened_at") or ((prior.get("created_at") or 0) - 3600)
            matched = _match_recovered_order(recent_orders, prior["ref_id"], psym, "buy",
                                             _w_start, prior.get("dollar_amount"),
                                             window_end=(prior.get("created_at") or 0) + 300)
            with db() as conn:
                if matched is not None:
                    # promote the row in place — the buy landed; ledger must count it
                    pj = {"_concord_recovered": True, "data": {"order": matched}}
                    px = prices.get(psym)
                    amt = prior.get("dollar_amount")
                    if px and amt:
                        pj["_concord_est_qty"] = round(amt / px, 6)
                    conn.execute("UPDATE executions SET status='placed', place_json=? "
                                 "WHERE id=? AND status='error'",
                                 (json.dumps(pj), prior["id"]))
                elif _feed_covers(recent_orders, orders_exhausted, prior.get("created_at")):
                    # verified absent — never re-place a non-basket buy; retire
                    # the evidence
                    conn.execute("UPDATE executions SET status='superseded' "
                                 "WHERE id=? AND status='error'", (prior["id"],))
                # Otherwise the feed simply did not reach back far enough to
                # prove anything. 'superseded' is terminal — club_shares counts
                # only 'placed' and reconcile drops the row from its candidates —
                # so retiring on an unproven absence would make a REAL fill
                # permanently invisible. Leave the row 'error': that is the same
                # state the unreadable-feed branch above leaves it in, and
                # reconcile's age-out is the backstop.
        # Compounding bookkeeping: once the BUY leg has run for this member,
        # the current stake figure has been honored — record it so the next
        # cycle's budget applies only future stake CHANGES as deltas. Only on
        # a completed (non-skipped) buy/both pass, and never touching
        # updated_at (the stake-freeze / demo clock).
        wrote_back = (compounding and will_place and phase in ("buy", "both")
                      and not msum.get("skip_reason") and stamped_stake is not None)
        if wrote_back:
            with db() as conn:
                # One transaction: the drain reads carry_pending and zeroes it,
                # and a credit landing between those two statements would
                # otherwise be zeroed without ever being counted.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # The commitment RIDES: after a deployed cycle the member's
                    # committed figure becomes what is actually deployed (or the
                    # matured capacity-clamped figure), so a $100 stake that grew
                    # to $105 reads "$105 committed" — and a percent stake converts
                    # to the dollars it resolved to. alloc_applied tracks the
                    # honored (daily-capped) budget so only future EDITS move it.
                    #
                    # The commitment UPDATE is a compare-and-swap: a mid-cycle
                    # stake edit (allowed for members who did not vote in the
                    # closed session) must survive. On a CAS miss only
                    # alloc_applied/slice_carry move, and the next cycle's
                    # compound_budget deploys cv + (edited_value − alloc_applied) —
                    # the member's edited absolute figure exactly.
                    value_target = commit_target if commit_target is not None else round(budget, 2)
                    if rides:
                        guard_type, guard_val = "dollar", stamped_stake
                    else:           # percent-bootstrap conversion: guard on run-start values
                        guard_type, guard_val = m["alloc_type"], float(m["alloc_value"] or 0.0)
                    conn.execute(
                        "UPDATE member_settings SET alloc_type='dollar', alloc_value=? "
                        "WHERE member_id=? AND alloc_type=? AND ABS(COALESCE(alloc_value,0)-?)<0.005",
                        (round(value_target, 2), mid, guard_type, guard_val))
                    # slice_carry is this cycle's OWN undeployed dollars, recomputed
                    # from scratch every buy leg — WRITTEN, never added to, so a
                    # second run of the same session cannot accumulate it.
                    # Dollars an EARLIER cycle failed to deploy arrive asynchronously
                    # (reconcile proving an errored order absent or dead at the
                    # broker) and wait in carry_pending. Draining them into a column
                    # the next run overwrites would DESTROY them, so the drain is
                    # remembered per session: every run of this cycle rebuilds the
                    # same figure, and a credit that lands between two runs simply
                    # adds to it.
                    _pending = _num((conn.execute(
                        "SELECT COALESCE(carry_pending,0) AS c FROM member_settings "
                        "WHERE member_id=?", (mid,)).fetchone() or {"c": 0})["c"]) or 0.0
                    _dkey = f"carry_drained:{sid}:{mid}"
                    _prev_drained = _num((conn.execute(
                        "SELECT value FROM kv WHERE key=?", (_dkey,)).fetchone()
                        or {"value": 0})["value"]) or 0.0
                    _drained = round(_prev_drained + _pending, 2)
                    # Crumb repairs spend account cash outside the commitment and are
                    # absorbed into the slice at the next valuation; the debit that
                    # nets them out is likewise session-scoped, or a re-run's
                    # overwrite would quietly capitalise the member's own dollars.
                    _rspent = _num((conn.execute(
                        "SELECT value FROM kv WHERE key=?", (f"repair_spend:{sid}:{mid}",)).fetchone()
                        or {"value": 0})["value"]) or 0.0
                    conn.execute(
                        "UPDATE member_settings SET alloc_applied=?, slice_carry=?, "
                        "carry_pending=0 WHERE member_id=?",
                        (round(budget, 2),
                         round(max(0.0, unplaced_usd - unsold_club_usd) + _drained - _rspent, 2),
                         mid))
                    conn.execute(
                        "INSERT INTO kv(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_dkey, str(_drained)))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                if _drained:
                    msum["carry_drained"] = _drained
            if not auth_stranded:
                _clear_strand(sid, mid)
        elif (will_place and phase in ("buy", "both")
              and not msum.get("skip_reason")):
            # A PINNED stake never rides carry — its budget is the committed
            # figure outright — so carry has no cycle to fund. Clear BOTH
            # columns: the compounding write-back is the only other writer of
            # slice_carry, so a figure left frozen here would be redeployed in
            # full the day the member switches compounding back on, long after
            # the pinned cycles already covered it.
            # alloc_applied is "the figure the executor last honored" and the
            # compounding ride deploys alloc_value − alloc_applied on top of
            # the marked slice. Pinned cycles honor the pinned budget, so they
            # record it too — otherwise a member who compounds, turns it off,
            # edits the stake, runs pinned, and turns it back on gets the edit
            # applied twice (a $100→$50 cut liquidates the slice).
            with db() as conn:
                conn.execute("UPDATE member_settings SET carry_pending=0, slice_carry=0, "
                             "alloc_applied=? WHERE member_id=?", (round(budget, 2), mid))
        # Sweep this member's sub-$1 crumbs while we are here. dust_cleanup is
        # the same review→place pipeline with its own DUST: ref_ids, so it is
        # idempotent and honours every gate run() just honoured; dry runs
        # preview it exactly like everything else. It existed only as a manual
        # admin button, which is why Neil's 0.000001-share MSFT and Charlie's
        # ALL sat in their accounts for days — nothing ever called it. Wrapped:
        # cosmetic cleanup must never take down a trading run.
        if phase in ("buy", "both") and not msum.get("skip_reason") and not auth_stranded:
            try:
                dust = dust_cleanup(mid, dry_run=not will_place, session_id=sid)
                if dust.get("orders"):
                    msum["dust"] = dust["orders"]
                # Charge repairs to the club slice: the $1 top-ups spend
                # account cash outside the commitment and are absorbed into
                # the club slice at the next valuation — the negative carry
                # nets them out so the committed stake stays flat
                # (alloc.compound_budget clamps the SUM at zero).
                if will_place and compounding:
                    _spent = sum(_REPAIR_BUY_USD for o in dust.get("orders", [])
                                 if o.get("side") == "buy" and o.get("status") == "placed")
                    if _spent:
                        # Recorded per session as well as applied: the debit
                        # lands AFTER the write-back, so a re-run of this cycle
                        # would otherwise overwrite it away and quietly
                        # capitalise the member's own repair dollars.
                        _rkey = f"repair_spend:{sid}:{mid}"
                        with db() as conn:
                            conn.execute("BEGIN IMMEDIATE")
                            try:
                                _tot = _num((conn.execute(
                                    "SELECT value FROM kv WHERE key=?", (_rkey,)).fetchone()
                                    or {"value": 0})["value"]) or 0.0
                                conn.execute(
                                    "INSERT INTO kv(key,value) VALUES(?,?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    (_rkey, str(round(_tot + _spent, 2))))
                                conn.execute("UPDATE member_settings SET slice_carry="
                                             "COALESCE(slice_carry,0) - ? WHERE member_id=?",
                                             (_spent, mid))
                                conn.execute("COMMIT")
                            except BaseException:
                                conn.execute("ROLLBACK")
                                raise
            except Exception as e:  # noqa: BLE001
                msum["dust_error"] = f"{type(e).__name__}: {e}"
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
            # A member stranded MID-PASS (token died between orders) leaves the
            # cycle genuinely unfinished — its sells placed and its buys did
            # not. Flipping there would hide the session from the next run's
            # crash-continuation, which is what redeploys those proceeds. A
            # member skipped BEFORE placing anything is not this case and must
            # never hold the flip (that is how sessions used to wedge); the
            # retirement sweep bounds the deferral either way.
            stranded = _stranded_members(sid)
            if err == 0 and not stranded and not summary.get("halted"):
                conn.execute("UPDATE vote_sessions SET status='executed' "
                             "WHERE id=? AND status='closed'", (sid,))
            elif summary.get("halted"):
                # The kill switch cut the loop short: the members it skipped
                # never had their half of the cycle. Flipping would hide the
                # session from the buy leg, leaving no way to finish it once
                # the operator lifts the halt.
                summary["executed_deferred"] = (
                    "halted mid-run — session kept open so the remaining members "
                    "can be executed once the halt is lifted")
            elif stranded:
                summary["executed_deferred"] = (
                    f"{len(stranded)} member(s) stranded mid-pass — session kept open "
                    f"so the next run can finish the cycle")
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


# Robinhood's fractional sell format is exactly six decimals, and its matching
# engine rejects quantities below a real minimum — a month of Charlie's ALL
# rejections (2026-08-11 → 09-10) proved 0.000001 shares is unsellable, over
# and over, as a daily push notification. Two rules fall out, shared by every
# sell path:
#
#   * floor_qty6() floors to 6dp WITH a 1e-9 epsilon: math.floor(0.00399*1e6)
#     is 3989 because the float is really 0.00398999…, and that off-by-one is
#     what MINTED the unsellable crumb — the exit sweep set sell_qty to the
#     full live balance and the raw floor chopped the last microshare back
#     off. The epsilon only repairs representation error; a genuine
#     0.0039896 still floors to 0.003989.
#   * MIN_SELL_QTY: a residue below this is left in place, recorded as
#     skipped, and never sent to the broker — a rejected order helps nobody
#     and pings the member's phone. Broker rejections were observed from
#     1e-6 up to 8e-6 sh; the smallest proven fill is 3.33e-4 sh; and the
#     rejection surfaces only at place time (review returned {} on a
#     rejected 1e-6 order), so the true boundary cannot be probed via
#     review. 1e-4 gives ~12x margin over the largest observed rejection,
#     forfeits nothing ever proven to fill, and everything in [1e-5,1e-4)
#     now routes to the $1 crumb repair instead of a doomed order.
MIN_SELL_QTY = 0.0001

# Crumb REPAIR (owner ask, 2026-09-11): an unsellable residue is topped up
# with the broker-minimum buy so it becomes an ordinary position; the NEXT
# cycle's sell leg then exits the whole thing first (the name is off the
# basket, and floor_qty6 sells the exact live quantity) before that day's
# buys run. Deliberately buy-today/sell-tomorrow, never a same-day round
# trip — that would burn a PDT day trade per repair. Cost per repair ≈ the
# spread on $1. Bounded per run so a pathological account can't turn the
# sweep into a shopping spree.
_REPAIR_BUY_USD = 1.00
_REPAIR_MAX_PER_RUN = 3
_REPAIR_MIN_BP = 2.00      # leave headroom — never spend the account's last dollar


def floor_qty6(qty: float) -> float:
    """Floor a share quantity to Robinhood's 6-decimal format, float-safely.

    Decimal over epsilon arithmetic: str() round-trips the float's shortest
    repr, so a broker-reported 6dp balance (0.00399, 536.152752) floors to
    itself EXACTLY at any magnitude — the earlier +1e-9 epsilon under-repaired
    above ~32 shares, where qty*1e6's own rounding error outgrows it, and
    quietly re-minted the crumb this function exists to prevent. A genuine
    7th-decimal digit still floors down (ROUND_FLOOR).
    """
    return float(Decimal(str(qty)).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR))


def dust_cleanup(member_id: int, dry_run: bool = True,
                 session_id: int | None = None) -> dict:
    """Sweep sub-$1 crumb positions left on club-traded names (est-vs-fill drift).

    Candidates = live long positions whose symbol appears in the member's club
    execution history (placed orders) AND live value ≤ $1.00 AND quantity < 0.01
    shares. NOTE these sizes are ALSO produced by full-target positions of
    small-stake members on high-priced names — which makes the in-basket guard
    on the sell path load-bearing, not cosmetic. Each candidate runs the same
    review→place→record pipeline as run(), with a deterministic ref_id keyed
    'DUST:{symbol}' under `session_id` — run() passes its executing session so
    every ref, guard and record keys to the session that actually trades; the
    admin route leaves it None and the sweep anchors to the newest DECIDED
    session (never an open ballot, whose refs/basket would disagree with
    run()'s). dry_run=True (default) reviews and returns the preview WITHOUT
    recording or placing; a real place additionally requires
    execution_mode=live + ARMED, exactly like run().
    """
    from ..calendar_us import at_et, is_trading_day, now_et, prev_trading_day, today_et
    with db() as conn:
        row = conn.execute(
            "SELECT account_number, status FROM rh_accounts WHERE member_id=?", (member_id,)
        ).fetchone()
        club_syms = {r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND status='placed'",
            (member_id,)
        ).fetchall()}
        if session_id is not None:
            latest_sid = session_id
        else:
            latest_sid = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM vote_sessions "
                "WHERE status IN ('closed','executed','lapsed')").fetchone()[0]
        _picks_row = conn.execute(
            "SELECT picks_json FROM vote_sessions WHERE id=?", (latest_sid,)
        ).fetchone()
    # The CURRENT basket: a crumb on an in-basket name needs no repair — the
    # resize/top-up path owns it; repair targets only names the club has
    # exited, whose top-up would otherwise never come. Distinguish UNKNOWN
    # (NULL/unparseable picks → conservative: no repairs at all) from a
    # genuinely empty basket.
    latest_basket: set[str] | None = None
    try:
        if _picks_row and _picks_row["picks_json"]:
            latest_basket = {p["symbol"] for p in json.loads(_picks_row["picks_json"])}
    except Exception:  # noqa: BLE001 — malformed picks_json must not kill the sweep
        latest_basket = None
    if latest_basket is None:
        with db() as conn:
            _fb = conn.execute(
                "SELECT picks_json FROM vote_sessions WHERE picks_json IS NOT NULL "
                "ORDER BY id DESC LIMIT 1").fetchone()
        try:
            if _fb:
                latest_basket = {p["symbol"] for p in json.loads(_fb["picks_json"])}
        except Exception:  # noqa: BLE001
            latest_basket = None
    basket_known = latest_basket is not None
    if latest_basket is None:
        latest_basket = set()
    if not row or not row["account_number"] or row["status"] != "connected":
        return {"ok": False, "error": "no connected Robinhood account",
                "member_id": member_id, "orders": []}
    acct = row["account_number"]
    will_place = settings.is_live and is_armed() and not dry_run
    # Standalone (admin-route) live sweeps run only during market hours: an
    # off-hours repair queues at the open and can fill into a PDT day trade.
    if session_id is None and not dry_run:
        _now = now_et()
        if not (is_trading_day()
                and at_et(today_et(), "09:30") <= _now <= at_et(today_et(), "16:00")):
            return {"ok": False, "error": ("market closed — repairs queue at the open and "
                                           "can fill into a PDT day trade; run during "
                                           "market hours"),
                    "member_id": member_id, "orders": []}
    try:
        client = vault.ensure_client(member_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"client unavailable: {type(e).__name__}: {e}",
                "member_id": member_id, "orders": []}
    live = positions.live_positions(member_id)
    prices = _quotes(client, [p["symbol"] for p in live])
    out = {"ok": True, "member_id": member_id, "dry_run": dry_run,
           "will_place": will_place, "orders": []}
    # Anything bought since the previous trading day's close must not be sold
    # today (same cutoff as run()'s sell-leg PDT guard).
    _pdt_cutoff = int(at_et(prev_trading_day(today_et()), "16:00").timestamp())
    repairs = 0                 # crumb top-ups placed/reviewed this run (bounded)
    repair_bp: float | None = None   # lazily fetched — most sweeps repair nothing
    for p in live:
        symbol, qty = p["symbol"], p["quantity"]
        if symbol not in club_syms or qty >= 0.01:
            continue
        price = prices.get(symbol)
        if price is None:
            # can't verify it's dust → leave it, on the report: a permanently
            # quoteless (delisted) crumb must not be invisible forever
            out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                  "note": "no quote — cannot verify dust"})
            continue
        if qty * price > 1.00:
            continue                       # a real position, not dust — silent
        ref_id = ref_id_for(latest_sid, member_id, f"DUST:{symbol}")
        # 6dp, not the old 8dp: Robinhood's quantity format is six decimals
        # (the sell path's comment has said so all along — this path sent
        # "0.00000100" anyway), and anything below MIN_SELL_QTY is the
        # unsellable-residue case: never hand the broker an order it will
        # reject onto the member's phone.
        qfloor = floor_qty6(qty)
        if qfloor <= 0:
            out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                  "note": "sub-1e-6 residue — below quantity floor"})
            continue
        if qfloor < MIN_SELL_QTY:
            # Unsellable on its own — REPAIR by topping up with the minimum
            # buy (see the constants' comment for the full story). Tomorrow's
            # sell leg exits the repaired position exactly; if anything here
            # half-lands, the position is then normal-sized and every
            # existing path handles it.
            entry = {"symbol": symbol, "side": "buy", "amount": f"{_REPAIR_BUY_USD:.2f}",
                     "note": "crumb repair — top up, next cycle exits the whole position"}
            fix_ref = ref_id_for(latest_sid, member_id, f"DUSTFIX:{symbol}")
            if not basket_known:
                # basket unknown → cannot tell a voted hold from an exited
                # crumb; defer every repair rather than risk topping up a name
                # the cycle owns
                entry.update(status="skipped", note="basket unknown — repair deferred")
                out["orders"].append(entry)
                continue
            if symbol in latest_basket:
                # Skip only when THIS member's buy leg actually reached the
                # name — a small-stake member whose picks[:n_eff] slice never
                # touches it would otherwise wait forever for an absorb that
                # never comes.
                # Any own-buy row counts, whatever its status: an errored or
                # deferred basket buy is still the buy leg's name to finish. A
                # repair there would supersede that row and mint a 'placed' buy
                # key that suppresses the real buy on the next run.
                with db() as conn:
                    _own_buy = conn.execute(
                        "SELECT 1 FROM executions WHERE ref_id=?",
                        (ref_id_for(latest_sid, member_id, symbol),)).fetchone()
                if _own_buy:
                    entry.update(status="skipped",
                                 note="crumb on a basket name — the buy leg absorbs it")
                    out["orders"].append(entry)
                    continue
            with db() as conn:
                # placed/reviewed/error only: a recorded SKIP must not block a
                # later same-session retry
                if conn.execute(
                    "SELECT 1 FROM executions WHERE ref_id=? "
                    "AND status IN ('placed','reviewed','error')", (fix_ref,)
                ).fetchone():
                    entry.update(status="skipped", note="repair already attempted this session")
                    out["orders"].append(entry)
                    continue
            if repairs >= _REPAIR_MAX_PER_RUN:
                if not dry_run:
                    _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "skipped",
                            error="repair budget for this run used up")
                entry.update(status="skipped", note="repair budget for this run used up")
                out["orders"].append(entry)
                continue
            if repair_bp is None:
                try:
                    _port = client.get_portfolio(acct)
                    _pd = _port.get("data", _port) if isinstance(_port, dict) else {}
                    _bp = _pd.get("buying_power") or {}
                    repair_bp = _num(_bp.get("buying_power") if isinstance(_bp, dict) else _bp) or 0.0
                except Exception:  # noqa: BLE001
                    repair_bp = 0.0
            if repair_bp < _REPAIR_MIN_BP:
                if not dry_run:
                    _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "skipped",
                            error="buying power too low to repair")
                entry.update(status="skipped", note="buying power too low to repair")
                out["orders"].append(entry)
                continue
            try:
                review = client.review_market_buy(acct, symbol, f"{_REPAIR_BUY_USD:.2f}")
            except Exception as e:  # noqa: BLE001
                # 'skipped', not 'error': nothing reached the broker, and an
                # error row would hold the session's executed-flip open —
                # review failures cluster on exactly these exited/halted names.
                if not dry_run:
                    _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "skipped",
                            error=f"repair review failed: {e}")
                entry.update(status="skipped", note=f"review failed: {e}")
                out["orders"].append(entry)
                continue
            blocked, alerts = has_blocking_alerts(review)
            if blocked:
                if not dry_run:
                    _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "skipped",
                            review=review, error="blocking pre-trade alert (repair)")
                entry.update(status="skipped", alerts=alerts)
                out["orders"].append(entry)
                continue
            if not will_place:
                if not dry_run:
                    _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "reviewed",
                            review=review)
                entry.update(status="reviewed")
                out["orders"].append(entry)
                repairs += 1
                continue
            try:
                place = client.place_market_buy(acct, symbol, f"{_REPAIR_BUY_USD:.2f}",
                                                ref_id=fix_ref)
                if isinstance(place, dict) and price:
                    place["_concord_est_qty"] = round(_REPAIR_BUY_USD / price, 6)
                _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "placed",
                        review=review, place=place)
                audit(f"member:{member_id}", "club_dust_repair", f"sym:{symbol}",
                      {"usd": _REPAIR_BUY_USD, "crumb_qty": qfloor})
                entry.update(status="placed")
                repairs += 1
                repair_bp -= _REPAIR_BUY_USD
            except Exception as e:  # noqa: BLE001
                _record(latest_sid, member_id, symbol, _REPAIR_BUY_USD, fix_ref, "error",
                        review=review, error=f"repair place: {e}")
                entry.update(status="error", note=str(e))
            out["orders"].append(entry)
            continue
        # An in-basket sub-$1 slice of a high-priced name is a voted hold,
        # not dust — the cycle's resize/top-up owns it.
        if symbol in latest_basket:
            out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                  "note": "in-basket position — the cycle owns it"})
            continue
        # Bought since the previous close (any session, any ref — plain buys,
        # repairs, recovered errors)? Selling it today is a PDT day trade.
        with db() as conn:
            _recent_buy = conn.execute(
                "SELECT 1 FROM executions WHERE member_id=? AND symbol=? AND side='buy' "
                "AND status IN ('placed','error') AND created_at >= ? LIMIT 1",
                (member_id, symbol, _pdt_cutoff)).fetchone()
        if _recent_buy:
            out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                  "note": "bought today — PDT guard"})
            continue
        # Repaired THIS session? Then this position was bought minutes-to-
        # hours ago by the crumb top-up — selling it now is the same-day
        # round trip (a PDT day trade) the repair design forbids. It exits
        # with the next session's sell leg instead. And if this session's
        # SELL leg is already exiting the name, a second dust sell of the
        # same shares would only bounce off the broker as insufficient —
        # skip that too (a pre-existing wart the repair tests surfaced).
        # A pending DUST sell of our own is skipped for the same reason: the
        # first place is still filling and a re-run must not double-sell.
        with db() as conn:
            if conn.execute(
                "SELECT 1 FROM executions WHERE status='placed' AND ref_id=?",
                (ref_id_for(latest_sid, member_id, f"DUSTFIX:{symbol}"),),
            ).fetchone():
                out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                      "note": "repaired this session — exits next cycle"})
                continue
            if conn.execute(
                "SELECT 1 FROM executions WHERE status IN ('placed','reviewed') AND ref_id=?",
                (ref_id_for(latest_sid, member_id, f"SELL:{symbol}"),),
            ).fetchone():
                out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                      "note": "already exiting this session"})
                continue
            if conn.execute(
                "SELECT 1 FROM executions WHERE status='placed' AND ref_id=?",
                (ref_id,),
            ).fetchone():
                out["orders"].append({"symbol": symbol, "side": "sell", "status": "skipped",
                                      "note": "dust sell already placed this session "
                                              "— awaiting fill"})
                continue
        qstr = f"{qfloor:.6f}"
        entry = {"symbol": symbol, "side": "sell", "qty": qstr,
                 "value": round(qty * price, 4), "ref_id": ref_id}
        try:
            review = client.review_market_sell(acct, symbol, qstr)
        except Exception as e:  # noqa: BLE001
            # 'skipped', not 'error': nothing reached the broker, and an error
            # row would hold the session's executed-flip open (same rule the
            # repair branch and both run() legs follow).
            if not dry_run:
                _record(latest_sid, member_id, symbol, None, ref_id, "skipped",
                        error=f"dust review failed: {e}", side="sell")
            entry.update(status="skipped", note=str(e))
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


def run_today(force_dry_run: bool = False, trigger: str = "schedule") -> dict:
    """Single-flight wrapper around the day's phase run.

    A file lock (finding #8) prevents a timer run and a dashboard 'Execute now'
    (or two quick clicks) from executing concurrently — the check-then-act on
    _placed_keys plus place()-before-record could otherwise let two runs both
    place the same order, relying only on broker ref_id dedup.

    `trigger` ('schedule' | 'manual:<email>') rides into the run_reports row.
    A lock-bounced call writes NO report — the run already in progress writes
    its own, so the invocation is not a run of its own.
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
            return _run_today_locked(force_dry_run=force_dry_run, trigger=trigger)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    finally:
        lf.close()


def _run_today_locked(force_dry_run: bool = False, trigger: str = "schedule") -> dict:
    """Run the phase scheduled for today: SELL on a sell/vote day (close the ballot,
    then exit dropped names), BUY on a buy day (top up the last sold basket with the
    now-settled cash). Idle days do nothing. This is what the cron + the dashboard
    'Execute now' button call."""
    from ..calendar_us import cycle_phase, today_et
    started_at = int(time.time())

    def _idle(summary: dict) -> dict:
        """A run that never reaches run(): persist the report here — EVERY
        invocation leaves a row, and an idle day's row is the proof the timer
        fired and correctly did nothing."""
        runreports.persist(summary, trigger=trigger, started_at=started_at)
        return summary

    phase = cycle_phase(today_et())
    if phase is None:
        return _idle({"ok": True, "note": "idle day — no sell or buy phase scheduled today",
                      "phase": "none", "orders": []})
    # Missed-window guard (G1): the execute timer is Persistent=true, so a
    # reboot spanning the scheduled fire re-runs this on boot. Inside the
    # trading morning that is exactly what we want (self-healed close + fresh
    # basket make a late fire safe); PAST the catch-up cutoff the day is gone —
    # trading the morning's consensus into the close is not the mandate.
    # Scheduled runs stand down and leave a loud trail; manual triggers bypass
    # (the human pressing the button is the judgment).
    if trigger == "schedule":
        import datetime as _dt
        from ..calendar_us import at_et, now_et
        try:
            cutoff = at_et(today_et(), settings.execute_catchup_until)
        except Exception:  # noqa: BLE001 — a bad knob must not block trading
            cutoff = at_et(today_et(), "15:45")
        # A cutoff at/before the scheduled fire time would stand down the
        # NORMAL run every cycle day — a misconfigured knob must never block
        # the week's trade. Give the on-time fire a 30-minute grace instead.
        try:
            exec_t = at_et(today_et(), settings.execute_at)
        except Exception:  # noqa: BLE001
            exec_t = None
        if exec_t is not None and cutoff <= exec_t:
            cutoff = exec_t + _dt.timedelta(minutes=30)
        now = now_et()
        if now >= cutoff:
            td = today_et().isoformat()
            note = (f"missed execute window — scheduled fire arrived at "
                    f"{now.strftime('%H:%M')} ET, past the "
                    f"{cutoff.strftime('%H:%M')} catch-up cutoff; no orders. "
                    "Run manually from the console if today's cycle should still trade.")
            # Audit ONCE per trade date: Persistent replays / repeated unit
            # starts must not storm the audit log (every fire still leaves its
            # own run_reports row via _idle — that is the proof it fired).
            with db() as conn:
                seen = conn.execute(
                    "SELECT 1 FROM audit_log WHERE action='missed_execute' "
                    "AND target=? LIMIT 1", (td,)).fetchone()
            if not seen:
                audit("system", "missed_execute", td,
                      {"phase": phase, "fired_at": now.strftime("%H:%M"),
                       "cutoff": cutoff.strftime("%H:%M")})
            return _idle({"ok": True, "note": note, "phase": phase,
                          "trade_date": td, "missed_window": True, "orders": []})
    if phase == "buy":
        session = voting.latest_session("closed")   # the basket sold on the prior sell day
        if not session:
            return _idle({"ok": True, "note": "no sold basket waiting to be bought",
                          "phase": phase, "orders": []})
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
            return _idle({"ok": True, "note": note, "phase": phase, "orders": [],
                          "stale_session": session.get("id")})
        return run(session, force_dry_run=force_dry_run, phase="buy", trigger=trigger)
    # 'sell' or 'both' — close today's ballot, then sell (and buy too, if 'both')
    session = voting.session_for_trade_date(today_et().isoformat())
    if not session:
        return _idle({"ok": True, "note": "no session for today", "phase": phase, "orders": []})
    if session["status"] == "open":
        session = voting.close_and_select(session["id"])
    return run(session, force_dry_run=force_dry_run, phase=phase, trigger=trigger)
