"""Ledger, reconciliation and session-retirement regressions (audit-fix batch).

The money that goes missing in this club never goes missing at the broker — it
goes missing in the BOOKKEEPING between a broker order and an execution row:

THE CARRY CONTRACT these tests are written against (two columns, two owners):

  member_settings.slice_carry   — THIS cycle's own undeployed dollars. WRITTEN,
     never added to, by the buy leg's compounding write-back. A re-run of the
     same session recomputes it from scratch, so it cannot accumulate.
  member_settings.carry_pending — dollars an EARLIER cycle failed to deploy,
     proven AT THE BROKER never to have landed. ADDITIVE, and reconcile_fills is
     its only writer; every credit is marked on the execution row it came from
     (_concord_carry_credited) so it can happen at most once per row. The
     write-back drains it: slice_carry = round(unplaced_usd + carry_pending, 2)
     with carry_pending zeroed in the same transaction.

  The split exists because five writers used to share slice_carry, mixing the
  executor's OVERWRITE semantics with asynchronous ADDITIVE credits: a second
  run of one session re-added the first run's carry, and the same dollars could
  be both re-deployed and credited. Nothing but the write-back touches
  slice_carry now, and nothing but reconcile touches carry_pending.

B1/Y1-Y3 — reconcile_fills: one broker order may be claimed by exactly ONE
     execution row, an 'error' row is promoted only when the matched order's
     size agrees with the row's own recorded intent, an order the broker
     positively killed RESOLVES its error row instead of leaving it in limbo,
     and an errored BUY's undeployed dollars land in carry_pending exactly once
     (never for a $1 DUSTFIX repair, and — retire and credit being ONE decision
     — the row is neither demoted nor marked while its session could still
     re-run and re-place the name at full size).
B2/B3 — ledger_trueup + positions.trueup_shares: a phantom is zeroed once, and
     the corrective row folds back into club_shares_derived so the SAME phantom
     is never re-detected on a later day (three duplicate TRUEUP rows drove 17
     member/symbol nets negative before the fold existed).
A6/X6/X7 — _retire_stale_sessions: a 'closed' session too old to trade lapses
     and its error rows are abandoned with their text intact, WITHOUT returning
     their dollars (only reconcile reads the broker, so only reconcile can prove
     an order never landed); the session being executed is never retired; and a
     dry run mutates nothing — not a session status, not a row, not a carry
     column.
C1 — the freeze predicates release once a session can no longer execute (the
     live session-29 lockout: nine days unable to edit a stake).

Run from backend/:  .venv/bin/python -m pytest tests/test_audit_ledger.py -q
(throwaway temp DB; only the broker surface — order feed, positions, quotes —
is mocked. positions.club_shares_derived itself is the real thing.)
"""
import datetime
import json
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import pytest  # noqa: E402

from app.calendar_us import today_et  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, positions, reconcile, voting  # noqa: E402

# Rows this module created, so teardown can drop exactly those.
_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    # Every test module shares one temp DB (CONCORD_DB_PATH via setdefault) and
    # the later ones run executor.run()/reconcile_all() over ALL members and
    # sessions. Drop only what this file made — its deliberately stale sessions
    # and errored rows must not colour anybody else's summary.
    with db() as conn:
        for mid in _MEMBERS:
            conn.execute("DELETE FROM executions WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM votes WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
        for sid in _SESSIONS:
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str) -> int:
    """A connected, agentic member. opted_in=0 throughout: nothing here needs the
    executor to fund the member, and an opted-in row would join later modules'
    run summaries (reconcile_all does not read opted_in, so reconciliation is
    exercised all the same)."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) VALUES(?,?,?,0,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,daily_cap_usd,max_orders,"
            "alloc_type,alloc_value,slice_carry,updated_at) "
            "VALUES(?,0,500.0,10,'dollar',50.0,0,?)", (mid, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?, 'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    _MEMBERS.append(mid)
    return mid


def _days_ago(n: int) -> str:
    return (today_et() - datetime.timedelta(days=n)).isoformat()


def _session_row(age_days: int, status: str = "closed") -> int:
    """A bare vote_sessions row `age_days` old (no ballot) — enough for the
    retirement sweep and for reconcile's session-terminal gate. trade_date is
    UNIQUE, so every call in this module uses its own age."""
    trade_date = _days_ago(age_days)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,?)",
            (trade_date, status, int(time.time()), int(time.time()), settings.top_n))
        sid = cur.lastrowid
    _SESSIONS.append(sid)
    return sid


def _ballot(trade_date: datetime.date, voter: int, picks: list[str]) -> dict:
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], voter, picks)
    return voting.close_and_select(s["id"])


def _rewind(member_id: int, days: int) -> None:
    """Age this member's whole execution history by `days`.

    ledger_trueup sees the clock through two ROW-level facts and nothing else:
    an execution inside 48h defers its symbol as possibly-in-flight, and a
    TRUEUP ref_id carries the UTC date so a same-day rerun dedupes on the ref
    alone. Rewinding the rows is therefore exactly what "a later day" means to
    this code — and it leaves the real guards (the trueup fold into the derived
    view, the coverage netting) as the only thing standing between a rerun and a
    duplicate phantom row.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT id, ref_id, created_at FROM executions WHERE member_id=?",
            (member_id,)).fetchall()
        for r in rows:
            ref = r["ref_id"] or ""
            if ref.startswith("TRUEUP:"):
                head, _, day = ref.rpartition(":")
                try:
                    shifted = datetime.date.fromisoformat(day) - datetime.timedelta(days=days)
                    ref = f"{head}:{shifted.isoformat()}"
                except ValueError:
                    pass
            conn.execute("UPDATE executions SET created_at=?, ref_id=? WHERE id=?",
                         (int(r["created_at"]) - days * 86400, ref, r["id"]))


def _backdate(member_id: int, days: int) -> None:
    """Age only the created_at stamps (refs untouched)."""
    with db() as conn:
        conn.execute(
            "UPDATE executions SET created_at=created_at-? WHERE member_id=?",
            (days * 86400, member_id))


def _rows(member_id: int, symbol: str | None = None) -> list[dict]:
    sql = "SELECT * FROM executions WHERE member_id=?"
    args: list = [member_id]
    if symbol is not None:
        sql += " AND symbol=?"
        args.append(symbol)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


def _pj(row: dict) -> dict:
    return json.loads(row["place_json"] or "{}")


def _carry(member_id: int) -> float:
    """This cycle's OWN undeployed dollars — the executor's write-back owns it."""
    with db() as conn:
        return float(conn.execute(
            "SELECT COALESCE(slice_carry,0) FROM member_settings WHERE member_id=?",
            (member_id,)).fetchone()[0])


def _pending(member_id: int) -> float:
    """Dollars an earlier cycle provably failed to deploy — reconcile's column.
    Every assertion about a broker-proven credit belongs here, not in _carry."""
    with db() as conn:
        return float(conn.execute(
            "SELECT COALESCE(carry_pending,0) FROM member_settings WHERE member_id=?",
            (member_id,)).fetchone()[0])


def _retire_session(sid: int, status: str = "executed") -> None:
    """Make a session terminal mid-test, the way a completed cycle (or the
    retirement sweep's 'lapsed') would — so reconcile's _session_retired gate
    flips between two passes over the SAME row."""
    with db() as conn:
        conn.execute("UPDATE vote_sessions SET status=? WHERE id=?", (status, sid))


def _sess_status(sid: int) -> str:
    with db() as conn:
        return conn.execute("SELECT status FROM vote_sessions WHERE id=?", (sid,)).fetchone()[0]


def _iso(ts: float | None = None) -> str:
    return datetime.datetime.fromtimestamp(ts or time.time(),
                                           datetime.timezone.utc).isoformat()


def _order(symbol, side, state, cum, *, agent="agentic", created=None, dollars=None,
           oid=None):
    """A broker order row (real RH returns ref_id=null, so reconcile matches on
    broker order id when it has one, else symbol+side+ET-date+closest time)."""
    o = {"symbol": symbol, "side": side, "state": state,
         "cumulative_quantity": str(cum), "placed_agent": agent,
         "created_at": created or _iso()}
    if dollars is not None:
        # the broker's real shape — never a flat dollar_amount
        o["dollar_based_amount"] = {"amount": f"{dollars:.6f}", "currency_code": "USD"}
    if oid is not None:
        o["id"] = oid
    return o


class FeedClient:
    """The broker surface these tests mock: the order feed, live positions and
    quotes. Everything downstream of it (club_shares_derived, trueup folding,
    the matcher) is the real implementation."""

    def __init__(self, orders=None, positions=None, quotes=None):
        self.orders = orders or []
        self.positions = positions or []      # (symbol, qty)
        self.quotes = quotes or {}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        orders = self.orders
        if placed_agent:   # the real API filters server-side
            orders = [o for o in orders if o.get("placed_agent", "agentic") == placed_agent]
        return {"data": {"orders": orders, "next": None}}

    def get_positions(self, acct):
        return {"data": {"positions": [
            {"symbol": s, "type": "long", "quantity": str(q)} for s, q in self.positions]}}

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes[s])}}
            for s in symbols if s in self.quotes]}}

    def get_portfolio(self, acct):
        return {"data": {"buying_power": {"buying_power": "0.00"}, "total_value": "0.00"}}


def _use(monkeypatch, client: FeedClient) -> None:
    monkeypatch.setattr(vault, "account_number_for", lambda m: f"ACC{m}")
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    positions.invalidate_feed_cache()


# ─── B2/B3: ledger_trueup folds back into the derived view ───────────────────

def test_ledger_trueup_is_idempotent_across_days(monkeypatch):
    """The three-duplicate-TRUEUP bug, end to end against the REAL derived view.

    The pre-fix loop: ledger_trueup zeroes a phantom by appending a $0 TRUEUP
    sell, but club_shares_derived read only the order feed — which still showed
    the original buy — so 48h later the SAME phantom was detected and zeroed
    again, each duplicate over-subtracting the ledger. positions.trueup_shares
    folds the corrective rows into the derived net, so the second run finds
    nothing to do and exactly one TRUEUP row ever exists.
    """
    mid = _member("tu-across@x.com")
    # Club history + the broker feed both say 0.5 AES; the account holds none.
    executor._record(_session_row(30, "executed"), mid, "AES", 100.0,
                     "tu-across-buy", "placed",
                     place={"data": {"order": {"cumulative_quantity": "0.5"}}})
    _backdate(mid, 5)          # past the 48h in-flight deferral
    _use(monkeypatch, FeedClient(orders=[_order("AES", "buy", "filled", "0.5",
                                                created=_iso(time.time() - 10 * 86400)),
                                         # the member sold it by hand — the phantom's origin
                                         _order("AES", "sell", "filled", "0.5", agent="user",
                                                created=_iso(time.time() - 8 * 86400))],
                                 positions=[("KO", "1.0")]))

    assert positions.club_shares(mid).get("AES") == pytest.approx(0.5)
    assert positions.club_shares_derived(mid).get("AES") == pytest.approx(0.5)

    first = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert [z["symbol"] for z in first["zeroed"]] == ["AES"]

    # …three days later. Rewinding the rows moves the only two clocks this code
    # reads (see _rewind), so the rerun is a genuine later-day rerun: neither the
    # 48h deferral nor the same-day ref_id dedup can be what saves it.
    _rewind(mid, 3)
    positions.invalidate_feed_cache()

    second = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)

    assert second["zeroed"] == [], "the phantom is already corrected — never zero it twice"
    trueups = [r for r in _rows(mid, "AES") if (r["ref_id"] or "").startswith("TRUEUP:")]
    assert len(trueups) == 1 and trueups[0]["side"] == "sell"
    assert positions.club_shares(mid).get("AES", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert "AES" not in positions.club_shares_derived(mid), \
        "the trueup must fold into the DERIVED net too, or it re-detects forever"


def test_trueup_coverage_nets_off_even_if_the_derived_view_goes_blind(monkeypatch):
    """Defence in depth (B2). The fold above is the real fix; the coverage guard
    is the belt: with a derived source that still reports the phantom (a future
    regression, or the live DB's pre-fold history), the prior TRUEUP row's
    quantity is netted off the detection so nothing is inserted twice."""
    mid = _member("tu-coverage@x.com")
    executor._record(_session_row(31, "executed"), mid, "AWK", 50.0,
                     "tu-cov-buy", "placed", place={"_concord_est_qty": 0.25})
    _backdate(mid, 5)
    _use(monkeypatch, FeedClient(orders=[_order("AWK", "buy", "filled", "0.25",
                                                created=_iso(time.time() - 10 * 86400)),
                                         _order("AWK", "sell", "filled", "0.25", agent="user",
                                                created=_iso(time.time() - 8 * 86400))],
                                 positions=[("KO", "1.0")]))
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"AWK": 0.25})

    assert reconcile.ledger_trueup(mid, account_number=f"ACC{mid}",
                                   dry_run=False)["zeroed"] != []
    _rewind(mid, 3)

    again = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)

    assert again["zeroed"] == [], "prior TRUEUP coverage must net off the phantom"
    assert len([r for r in _rows(mid, "AWK")
                if (r["ref_id"] or "").startswith("TRUEUP:")]) == 1


# ─── B1: one broker order, one execution row ─────────────────────────────────

def test_one_broker_order_is_claimed_by_exactly_one_row(monkeypatch):
    """Two same-day rows for one name cannot both reconcile against a single
    broker order — the claim is 1:1, so the loser stays on its estimate and is
    counted unmatched rather than double-counting the fill into the ledger."""
    mid = _member("rec-claim@x.com")
    sid = _session_row(20)
    executor._record(sid, mid, "AAPL", 100.0, "claim-a", "placed",
                     place={"_concord_est_qty": 1.0})
    executor._record(sid, mid, "AAPL", 100.0, "claim-b", "placed",
                     place={"_concord_est_qty": 1.0})
    _use(monkeypatch, FeedClient(orders=[_order("AAPL", "buy", "filled", "0.9")]))

    res = reconcile.reconcile_fills(mid)

    assert res["changed"] == 1 and len(res["reconciled"]) == 1
    assert res["unmatched"] == 1
    reconciled = [r for r in _rows(mid, "AAPL") if _pj(r).get("_concord_reconciled")]
    assert len(reconciled) == 1
    # 0.9 (the real fill) + 1.0 (the unmatched row's surviving estimate)
    assert positions.club_shares(mid)["AAPL"] == pytest.approx(1.9)


def test_placed_rows_claim_before_error_rows(monkeypatch):
    """Claim ORDER matters as much as the 1:1 rule: the row created seconds from
    the order must beat a stale error row for the same name. Here the single
    filled order also matches the error row's dollar intent exactly — if the
    error row claimed it first it would be promoted, and the ledger would count
    one broker fill twice."""
    mid = _member("rec-priority@x.com")
    sid = _session_row(19)
    executor._record(sid, mid, "TSLA", 250.0, "prio-placed", "placed",
                     place={"_concord_est_qty": 1.0})
    executor._record(sid, mid, "TSLA", 250.0, executor.ref_id_for(sid, mid, "TSLA"),
                     "error", error="place: boom")
    _use(monkeypatch, FeedClient(
        orders=[_order("TSLA", "buy", "filled", "0.95", dollars=250.0)]))

    res = reconcile.reconcile_fills(mid)

    assert len(res["reconciled"]) == 1 and res["promoted"] == []
    statuses = {r["ref_id"]: r["status"] for r in _rows(mid, "TSLA")}
    assert statuses["prio-placed"] == "placed"
    assert statuses[executor.ref_id_for(sid, mid, "TSLA")] == "error"
    assert positions.club_shares(mid)["TSLA"] == pytest.approx(0.95)


def test_row_with_a_broker_order_id_binds_to_that_order_only(monkeypatch):
    """Once a pass stamps _concord_broker_order_id the row re-binds EXACTLY, not
    by proximity — otherwise a later, nearer same-name order would steal the
    match and rewrite the row's quantity to another order's fill."""
    mid = _member("rec-bind@x.com")
    sid = _session_row(18)
    executor._record(sid, mid, "GOOGL", 200.0, "bind-ref", "placed",
                     place={"_concord_est_qty": 1.0,
                            "_concord_broker_order_id": "ord-mine"})
    _use(monkeypatch, FeedClient(orders=[
        _order("GOOGL", "buy", "filled", "5.0", oid="ord-other"),                 # nearer
        _order("GOOGL", "buy", "filled", "2.0", oid="ord-mine",
               created=_iso(time.time() - 90 * 60)),
    ]))

    res = reconcile.reconcile_fills(mid)

    assert len(res["reconciled"]) == 1
    assert positions.club_shares(mid)["GOOGL"] == pytest.approx(2.0), \
        "bound to its own order, not the closest one in time"


def test_error_buy_promoted_only_when_the_order_size_agrees(monkeypatch):
    """An 'error' row is promoted to 'placed' only when the matched order's
    dollar size agrees with the row's own recorded intent — a same-name order
    from another cycle must not resurrect it (that promotion double-counts the
    ledger and the club then sells shares it never bought)."""
    agree = _member("rec-agree@x.com")
    sid_a = _session_row(21)
    executor._record(sid_a, agree, "MSFT", 500.0, executor.ref_id_for(sid_a, agree, "MSFT"),
                     "error", error="place: connection reset")
    _use(monkeypatch, FeedClient(
        orders=[_order("MSFT", "buy", "filled", "1.25", dollars=500.0)]))

    res = reconcile.reconcile_fills(agree)

    assert len(res["promoted"]) == 1
    assert _rows(agree, "MSFT")[0]["status"] == "placed"
    assert positions.club_shares(agree)["MSFT"] == pytest.approx(1.25)

    disagree = _member("rec-disagree@x.com")
    sid_d = _session_row(22)
    executor._record(sid_d, disagree, "MSFT", 500.0,
                     executor.ref_id_for(sid_d, disagree, "MSFT"),
                     "error", error="place: connection reset")
    _use(monkeypatch, FeedClient(
        orders=[_order("MSFT", "buy", "filled", "0.6", dollars=250.0)]))

    res2 = reconcile.reconcile_fills(disagree)

    assert res2["promoted"] == [] and res2["unmatched"] >= 1
    assert _rows(disagree, "MSFT")[0]["status"] == "error", "evidence survives a bad match"
    assert "MSFT" not in positions.club_shares(disagree)


# ─── Y1/Y3: a dead broker order RESOLVES its error row ───────────────────────

def test_error_buy_on_a_dead_order_is_retired_and_credited_once(monkeypatch):
    """Pre-fix limbo: the matcher bound an 'error' buy to a cancelled order, and
    because it WAS matched the aging pass never saw it again — the row waited
    forever and its dollars left the member's slice silently. A dead order is
    proof the buy never landed, so once the session is past re-running, retire
    the row and credit the dollars ONCE.

    They go to carry_pending, not slice_carry: slice_carry holds the CURRENT
    cycle's own undeployed figure and the buy leg's write-back overwrites it
    wholesale, so an asynchronous credit written there is either erased by the
    next write-back or (on a re-run) re-added on top of itself. carry_pending is
    additive, reconcile is its only writer, and the write-back drains it into
    slice_carry exactly once. The per-row _concord_carry_credited mark plus the
    _concord_aged_out skip are what make the credit at-most-once.
    """
    mid = _member("rec-dead@x.com")
    sid = _session_row(23)                     # 23 days old — long past re-running
    executor._record(sid, mid, "NVDA", 40.0, executor.ref_id_for(sid, mid, "NVDA"),
                     "error", error="place: timeout")
    _use(monkeypatch, FeedClient(
        orders=[_order("NVDA", "buy", "cancelled", "0", dollars=40.0)]))

    res = reconcile.reconcile_fills(mid)

    assert [a["symbol"] for a in res["aged"]] == ["NVDA"]
    assert res["carry_credited"] == [{"symbol": "NVDA", "amount": 40.0, "session_id": sid}]
    row = _rows(mid, "NVDA")[0]
    assert row["status"] == "skipped"
    assert row["error"].startswith("verified dead at broker (cancelled);")
    assert row["error"].endswith("place: timeout"), "the original text is preserved"
    assert _pj(row)["_concord_aged_out"] is True
    assert _pj(row)["_concord_carry_credited"] == pytest.approx(40.0), \
        "the credit is marked on the row it came from — that mark IS the guard"
    assert _pending(mid) == pytest.approx(40.0)
    assert _carry(mid) == pytest.approx(0.0), \
        "reconcile never writes slice_carry — the write-back owns that column"

    res2 = reconcile.reconcile_fills(mid)                 # a later pass must not re-credit

    assert res2["carry_credited"] == []
    assert _pending(mid) == pytest.approx(40.0)
    assert _carry(mid) == pytest.approx(0.0)


def test_dead_order_buy_on_a_live_session_is_neither_retired_nor_credited(monkeypatch):
    """Retiring the row and returning its dollars are ONE decision, not two.

    A buy the broker created then rejected is proof the money never left — but
    while its session can still re-run, that re-run re-places the name at full
    size, so crediting the dollars as well would deploy them twice: once into
    the position, once into the next cycle's budget. The earlier design retired
    the row anyway and only withheld the credit — which is worse than either
    half alone: a 'skipped' row carrying _concord_aged_out is never re-scanned,
    so the dollars could NEVER be credited afterwards and simply vanished from
    the member's slice.

    So while _session_retired is False the dead-order branch does nothing at
    all: the row stays 'error', unmarked, counted as pending, and is
    re-evaluated on a later pass (see the lifecycle test below, which walks the
    same row through the moment its session goes terminal).
    """
    mid = _member("rec-dead-live@x.com")
    sid = _session_row(2)                      # two days old — still re-runnable
    executor._record(sid, mid, "NVDA", 40.0, executor.ref_id_for(sid, mid, "NVDA"),
                     "error", error="place: timeout")
    _use(monkeypatch, FeedClient(
        orders=[_order("NVDA", "buy", "rejected", "0", dollars=40.0)]))

    res = reconcile.reconcile_fills(mid)

    assert res["aged"] == [], "the decision waits — retiring here would strand the dollars"
    assert res["carry_credited"] == []
    assert res["pending"] >= 1
    row = _rows(mid, "NVDA")[0]
    assert row["status"] == "error", "the row must stay re-evaluable"
    assert row["error"] == "place: timeout", "untouched, so a later pass sees it whole"
    assert "_concord_aged_out" not in _pj(row)
    assert "_concord_carry_credited" not in _pj(row)
    assert _pending(mid) == pytest.approx(0.0)
    assert _carry(mid) == pytest.approx(0.0)


def test_dead_order_row_credits_exactly_once_across_three_passes(monkeypatch):
    """The whole life of one dead-order row, which the two tests above only see
    half of each — and the shape the old design got wrong.

    Pass 1 (session still live): retire+credit is one decision and it is not yet
    takeable, so nothing happens — the row is still 'error' and unmarked, so
    pass 2 can still reach it. That reachability is the point: under the old
    "retire now, credit later" split the row would be 'skipped' with
    _concord_aged_out set, reconcile's candidate query would never fetch it
    again, and the $40 would be gone from the member's budget permanently.
    Pass 2 (session terminal): the same row now demotes to 'skipped', is marked
    _concord_aged_out + _concord_carry_credited, and carry_pending gains $40.
    Pass 3: the marks hold — no second credit, on the same feed, same row.
    """
    mid = _member("rec-dead-lifecycle@x.com")
    sid = _session_row(5)          # inside the 5-day bound → NOT retired yet
    ref = executor.ref_id_for(sid, mid, "NVDA")
    executor._record(sid, mid, "NVDA", 40.0, ref, "error", error="place: timeout")
    _use(monkeypatch, FeedClient(
        orders=[_order("NVDA", "buy", "cancelled", "0", dollars=40.0)]))

    first = reconcile.reconcile_fills(mid)

    assert first["aged"] == [] and first["carry_credited"] == []
    assert _rows(mid, "NVDA")[0]["status"] == "error"
    assert _pending(mid) == pytest.approx(0.0)

    _retire_session(sid, "executed")            # the cycle finishes; it can never re-place
    second = reconcile.reconcile_fills(mid)

    assert [a["symbol"] for a in second["aged"]] == ["NVDA"]
    assert second["carry_credited"] == [{"symbol": "NVDA", "amount": 40.0,
                                         "session_id": sid}]
    row = _rows(mid, "NVDA")[0]
    assert row["status"] == "skipped"
    assert row["error"].startswith("verified dead at broker (cancelled);")
    assert row["error"].endswith("place: timeout")
    assert _pj(row)["_concord_aged_out"] is True
    assert _pj(row)["_concord_carry_credited"] == pytest.approx(40.0)
    assert _pending(mid) == pytest.approx(40.0)
    assert _carry(mid) == pytest.approx(0.0)

    third = reconcile.reconcile_fills(mid)

    assert third["aged"] == [] and third["carry_credited"] == []
    # _concord_aged_out drops the row from candidacy outright, so the third pass
    # finds nothing to scan at all — it cannot re-credit even by accident.
    assert third.get("changed", 0) == 0
    assert third["unmatched"] == 0 and third["pending"] == 0
    assert _pending(mid) == pytest.approx(40.0), "additive column, credited once"
    assert _carry(mid) == pytest.approx(0.0)


def test_partially_filled_then_cancelled_buy_keeps_its_shares(monkeypatch):
    """"Cancelled" is not "never filled".

    A dollar market buy is a day order: it can fill in PART and have the
    remainder cancelled at the close — the normal end state for a thin name.
    Zeroing the quantity there would drop real shares out of the club position
    (so a later exit could only reach them through the member's personal
    holding), while ALSO returning the whole amount to the slice would deploy
    the same dollars a second time. The filled part is kept and counted, and
    only the provably unfilled remainder can ever be credited.
    """
    mid = _member("rec-partial@x.com")
    sid = _session_row(26, "executed")           # terminal: credits may pay out
    executor._record(sid, mid, "ADM", 50.0, executor.ref_id_for(sid, mid, "ADM"),
                     "error", error="place: timeout")
    o = _order("ADM", "buy", "cancelled", "0.35", dollars=50.0)
    # $30 of the $50 actually filled — said the way the broker says it: through
    # the order's executions (there is no executed_notional field in the feed)
    o["executions"] = [{"price": "85.714286", "quantity": "0.35"}]
    _use(monkeypatch, FeedClient(orders=[o]))

    res = reconcile.reconcile_fills(mid)

    row = _rows(mid, "ADM")[0]
    assert row["status"] == "placed", "the shares are real — the row is a holding"
    assert _pj(row)["data"]["order"]["cumulative_quantity"] == pytest.approx(0.35)
    assert res["carry_credited"] == [{"symbol": "ADM", "amount": 20.0, "session_id": sid}]
    assert _pending(mid) == pytest.approx(20.0), "only the unfilled $20 comes back"


def test_partial_fill_without_an_executed_notional_credits_nothing(monkeypatch):
    """Splitting $50 into filled and unfilled needs a figure from the broker.
    Without one, any split is a guess — and a guess that runs high MINTS money
    into the member's committed slice. Keep the shares, credit nothing, and let
    the next valuation see the fill in club_value."""
    mid = _member("rec-partial-noamt@x.com")
    sid = _session_row(27, "executed")
    executor._record(sid, mid, "ADM", 50.0, executor.ref_id_for(sid, mid, "ADM"),
                     "error", error="place: timeout")
    _use(monkeypatch, FeedClient(
        orders=[_order("ADM", "buy", "cancelled", "0.35", dollars=50.0)]))

    res = reconcile.reconcile_fills(mid)

    assert _rows(mid, "ADM")[0]["status"] == "placed"
    assert res["carry_credited"] == []
    assert _pending(mid) == pytest.approx(0.0)


def test_placed_buy_killed_by_the_broker_parks_then_pays_its_claim(monkeypatch):
    """A buy we recorded 'placed' that the broker later cancels outright.

    Two things must happen at different times. The LEDGER has to stop claiming
    the shares immediately — a phantom holding is what lets a later exit reach
    into the member's own position — but the DOLLARS cannot come back while the
    session could still re-place the order, or they would be deployed and
    credited. So the claim is parked on the row and paid only once the session
    is past re-placing it.
    """
    mid = _member("rec-placed-dead@x.com")
    sid = _session_row(0)                         # TODAY's cycle: still 'closed' and
                                                  # very much able to run again
    executor._record(sid, mid, "KO", 20.0, executor.ref_id_for(sid, mid, "KO"),
                     "placed", place={"_concord_est_qty": 0.4})
    client = FeedClient(orders=[_order("KO", "buy", "rejected", "0", dollars=20.0)])
    _use(monkeypatch, client)

    res1 = reconcile.reconcile_fills(mid)

    row = _rows(mid, "KO")[0]
    assert row["status"] == "skipped", "the ledger stops counting it at once"
    assert _pj(row)["_concord_awaiting_credit"] == pytest.approx(20.0)
    assert res1["carry_credited"] == []
    assert _pending(mid) == pytest.approx(0.0), "not while the session could re-place it"

    _retire_session(sid, "lapsed")
    res2 = reconcile.reconcile_fills(mid)

    assert res2["carry_credited"] == [{"symbol": "KO", "amount": 20.0, "session_id": sid}]
    assert _pending(mid) == pytest.approx(20.0)
    row = _rows(mid, "KO")[0]
    assert "_concord_awaiting_credit" not in _pj(row)
    assert _pj(row)["_concord_carry_credited"] == pytest.approx(20.0)

    reconcile.reconcile_fills(mid)                # a third pass pays nothing more
    assert _pending(mid) == pytest.approx(20.0)


def test_dustfix_repair_buy_is_retired_without_crediting_the_slice(monkeypatch):
    """A $1 crumb repair is account cash fronted OUTSIDE the commitment — only a
    PLACED repair is charged to the slice, so crediting an unplaced one would
    inflate the member's stake by $1 per failed repair. The row still retires
    (its session is long past re-running, so the retire half of the decision is
    unblocked); the credit is suppressed by _is_repair_row, and suppressed means
    NEITHER carry column moves."""
    mid = _member("rec-dustfix@x.com")
    sid = _session_row(24)
    fix_ref = executor.ref_id_for(sid, mid, "DUSTFIX:KO")
    executor._record(sid, mid, "KO", executor._REPAIR_BUY_USD, fix_ref, "error",
                     error="place: rejected")
    _use(monkeypatch, FeedClient(orders=[_order("KO", "buy", "rejected", "0", dollars=1.0)]))

    res = reconcile.reconcile_fills(mid)

    assert [a["symbol"] for a in res["aged"]] == ["KO"]
    assert res["carry_credited"] == []
    assert _pending(mid) == pytest.approx(0.0) and _carry(mid) == pytest.approx(0.0)
    assert "_concord_carry_credited" not in _pj(_rows(mid, "KO")[0])
    assert _rows(mid, "KO")[0]["status"] == "skipped"


# ─── Y2: a buy only ages out once its session can no longer re-run ───────────

def _coverage_feed() -> FeedClient:
    """A feed whose OLDEST order predates the row under test — reconcile only
    ages an unmatched BUY when the fetched window provably covers it (pagination
    is bounded, so a thin feed is not evidence of absence). The order is on an
    unrelated name so it can never be the match itself."""
    return FeedClient(orders=[_order("SPY", "buy", "filled", "1.0",
                                     created=_iso(time.time() - 10 * 86400))])


def test_fresh_sessions_errored_buy_is_never_aged_out(monkeypatch):
    """The double-deploy the aging gate exists to stop: demoting the row to
    'skipped' makes it replanable, so a live re-run of the session would re-place
    the name at full size AND keep the credited dollars. While the session can
    still run, an unmatched error row is left exactly as it is — and with it,
    both carry columns (reconcile's carry_pending, which is the only place a
    broker-proven credit ever lands, and the executor-owned slice_carry)."""
    mid = _member("age-fresh@x.com")
    sid = _session_row(4)               # inside STALE_BASKET_DAYS → can re-run
    executor._record(sid, mid, "AMZN", 30.0, executor.ref_id_for(sid, mid, "AMZN"),
                     "error", error="place: boom")
    _backdate(mid, 4)                              # older than the 3-day age-out window
    _use(monkeypatch, _coverage_feed())

    res = reconcile.reconcile_fills(mid)

    assert res["aged"] == [] and res["carry_credited"] == []
    assert res["unmatched"] == 1
    assert _rows(mid, "AMZN")[0]["status"] == "error"
    assert _pending(mid) == pytest.approx(0.0) and _carry(mid) == pytest.approx(0.0)


def test_errored_buy_ages_out_once_its_session_is_terminal_or_stale(monkeypatch):
    """Same row, sessions that can no longer place: an 'executed' session and a
    'closed' one past STALE_BASKET_DAYS. Now the dollars provably never deployed
    and can never deploy, so the row retires and the member gets them back.

    "Back" means carry_pending — the additive column reconcile owns. slice_carry
    is the executor's per-cycle recompute and must stay untouched here: writing
    the credit there would be erased by the next write-back, or re-added on a
    re-run of the same session. The next buy leg drains carry_pending into
    slice_carry (slice_carry = unplaced_usd + carry_pending, pending zeroed) in
    one transaction, which is where these dollars actually re-enter the budget.
    """
    done = _member("age-executed@x.com")
    sid_done = _session_row(3, "executed")
    executor._record(sid_done, done, "AMZN", 30.0, executor.ref_id_for(sid_done, done, "AMZN"),
                     "error", error="place: boom")
    _backdate(done, 4)

    stale = _member("age-stale@x.com")
    sid_stale = _session_row(9)
    executor._record(sid_stale, stale, "AMZN", 12.5,
                     executor.ref_id_for(sid_stale, stale, "AMZN"),
                     "error", error="place: boom")
    _backdate(stale, 4)
    _use(monkeypatch, _coverage_feed())

    res_done = reconcile.reconcile_fills(done)
    res_stale = reconcile.reconcile_fills(stale)

    for res in (res_done, res_stale):
        assert [a["symbol"] for a in res["aged"]] == ["AMZN"]
    assert _rows(done, "AMZN")[0]["status"] == "skipped"
    assert _rows(done, "AMZN")[0]["error"].startswith("aged out: no broker match")
    assert _pj(_rows(done, "AMZN")[0])["_concord_carry_credited"] == pytest.approx(30.0)
    assert _pending(done) == pytest.approx(30.0)
    assert _pending(stale) == pytest.approx(12.5)
    assert _carry(done) == pytest.approx(0.0) and _carry(stale) == pytest.approx(0.0), \
        "reconcile never writes slice_carry"


# ─── A6/X6/X7: stale-session retirement ──────────────────────────────────────

def test_stale_closed_session_lapses_and_abandons_without_crediting(monkeypatch):
    """A 'closed' session older than STALE_BASKET_DAYS can never trade (the buy
    leg refuses it), so leaving it open froze its voters and re-fed its error
    rows to every reconcile pass. Retirement flips it to 'lapsed' and abandons
    the error rows with their text intact.

    It does NOT return their dollars — executor._credit_abandoned_buys is gone.
    Retirement is a clock, not evidence: it never reads the broker, so an
    'error' row it abandons may perfectly well be an order that LANDED (the
    write failed after the broker accepted it), and crediting that one puts the
    same money in the member's budget twice. Only reconcile_fills reads the
    order feed, so only reconcile can prove an order never landed, and only
    reconcile credits (into carry_pending). The cost of the rule is that an
    unreachable member's undeployed dollars stay out of their slice until a
    reconcile pass can prove otherwise — the conservative direction, and the
    only one that cannot double-deploy real money.
    """
    mid = _member("lapse-main@x.com")
    stale = _session_row(12)
    executor._record(stale, mid, "AAPL", 25.0, executor.ref_id_for(stale, mid, "AAPL"),
                     "error", error="place: connection reset")
    executor._record(stale, mid, "KO", executor._REPAIR_BUY_USD,
                     executor.ref_id_for(stale, mid, "DUSTFIX:KO"), "error",
                     error="place: rejected")
    fresh = _session_row(1)
    executor._record(fresh, mid, "AAPL", 30.0, executor.ref_id_for(fresh, mid, "AAPL"),
                     "error", error="place: boom")

    executor._retire_stale_sessions()

    assert _sess_status(stale) == "lapsed"
    stale_rows = {r["symbol"]: r for r in _rows(mid) if r["session_id"] == stale}
    assert {s: r["status"] for s, r in stale_rows.items()} == {"AAPL": "abandoned",
                                                               "KO": "abandoned"}
    assert stale_rows["AAPL"]["error"] == "place: connection reset"
    assert _pending(mid) == pytest.approx(0.0), "retirement is a clock, not broker proof"
    assert _carry(mid) == pytest.approx(0.0)
    assert not any("_concord_carry_credited" in _pj(r) for r in stale_rows.values())
    assert _sess_status(fresh) == "closed"
    assert [r["status"] for r in _rows(mid) if r["session_id"] == fresh] == ["error"]
    assert not hasattr(executor, "_credit_abandoned_buys"), \
        "the retirement-side credit path is deleted, not merely unused"

    executor._retire_stale_sessions()                     # sweeping again is a no-op

    assert _pending(mid) == pytest.approx(0.0) and _carry(mid) == pytest.approx(0.0)


def test_session_being_executed_is_never_retired():
    """A manual run of an old session must not lapse the very cycle it is
    executing — current_sid is exempt from the sweep."""
    sid = _session_row(14)

    executor._retire_stale_sessions(current_sid=sid)
    assert _sess_status(sid) == "closed"

    executor._retire_stale_sessions(current_sid=sid + 10_000)
    assert _sess_status(sid) == "lapsed", "any other run does retire it"


def test_dry_run_retires_nothing(monkeypatch):
    """An inspection dry run must not mutate sessions, error rows or kv — the
    sweep is gated on will_place, not merely on reaching run()."""
    voter = _member("lapse-dry@x.com")
    stale = _session_row(15)
    executor._record(stale, voter, "TSLA", 15.0, executor.ref_id_for(stale, voter, "TSLA"),
                     "error", error="place: boom")
    closed = _ballot(today_et() + datetime.timedelta(days=40), voter, ["AAPL"])
    _use(monkeypatch, FeedClient(quotes={"AAPL": 100.0}))

    executor.run(closed, force_dry_run=True, phase="both")

    assert _sess_status(stale) == "closed"
    assert [r["status"] for r in _rows(voter) if r["session_id"] == stale] == ["error"]
    assert _pending(voter) == pytest.approx(0.0) and _carry(voter) == pytest.approx(0.0)


def test_dry_run_mutates_nothing_at_all(monkeypatch):
    """The stronger form of the test above, covering the OTHER writer a run
    reaches: reconcile.

    run() calls reconcile_all(dry_run=not will_place). Before that argument
    existed the sweep ran for real inside an inspection dry run — so a
    force_dry_run=True pass would demote error rows to 'skipped', stamp them
    _concord_aged_out and add to carry_pending, silently changing the state an
    operator was only trying to LOOK at (and, because the marks are the
    at-most-once guard, doing it under a mode that is supposed to place nothing).

    The fixture is exactly the state that mutates on a live pass: a stale
    sibling session whose errored buy is past the age-out window, against a feed
    whose oldest order predates the row (reconcile's coverage proof). After the
    dry run every row status, error text, place_json, carry column and session
    status must be byte-identical. The real reconcile pass at the end is the
    control: it proves the fixture was genuinely agable, so the "nothing
    changed" assertions above it are not vacuously true.
    """
    voter = _member("dry-nomutate@x.com")
    stale = _session_row(16)
    executor._record(stale, voter, "TSLA", 15.0, executor.ref_id_for(stale, voter, "TSLA"),
                     "error", error="place: boom")
    _backdate(voter, 4)                       # older than the 3-day age-out window
    closed = _ballot(today_et() + datetime.timedelta(days=41), voter, ["AAPL"])
    feed = _coverage_feed()
    feed.quotes = {"AAPL": 100.0}
    _use(monkeypatch, feed)

    before = {r["id"]: (r["status"], r["error"], r["place_json"]) for r in _rows(voter)}
    # Wrap rather than replace: the REAL sweep still runs (so the assertions
    # below are about its behaviour, not a stub's), we only record how run()
    # called it. run() swallows reconcile errors, so a silent skip would make
    # the test vacuous — the recorded arg is what rules that out.
    seen: list[bool] = []
    real_all = reconcile.reconcile_all

    def _spy(dry_run: bool = False):
        seen.append(dry_run)
        return real_all(dry_run=dry_run)

    monkeypatch.setattr(reconcile, "reconcile_all", _spy)

    executor.run(closed, force_dry_run=True, phase="both")

    assert seen == [True], "run() must pass dry_run=not will_place, and must call it"
    assert {r["id"]: (r["status"], r["error"], r["place_json"])
            for r in _rows(voter)} == before, "a dry run rewrites no execution row"
    assert _sess_status(stale) == "closed" and _sess_status(closed["id"]) == "closed"
    assert _pending(voter) == pytest.approx(0.0), "reconcile_all ran dry — no credit"
    assert _carry(voter) == pytest.approx(0.0)

    reconcile.reconcile_fills(voter)          # control: the same row DOES age live

    assert _rows(voter, "TSLA")[0]["status"] == "skipped"
    assert _pending(voter) == pytest.approx(15.0)


# ─── C1: the freeze predicates release ───────────────────────────────────────

def test_freeze_releases_once_a_later_session_has_executed():
    """The live session-29 lockout: session ids are AUTOINCREMENT in trade-date
    order, so a 'closed' session older than the newest EXECUTED one can never
    execute — freezing its voters protects nothing and locked the owner out of
    /me/allocation, /me/participate and /me/compound for nine days."""
    mid = _member("freeze-exec@x.com")
    _ballot(today_et() + datetime.timedelta(days=50), mid, ["AAPL"])

    assert voting.member_in_closed_unexecuted_session(mid) is True
    assert voting.member_has_live_ballot(mid) is True

    later = _session_row(-51, "executed")

    assert later > 0
    assert voting.member_in_closed_unexecuted_session(mid) is False, \
        "the stuck session is behind an executed one — it can never execute"
    assert voting.member_has_live_ballot(mid) is False


def test_lapsed_session_releases_its_voters():
    """The other release path: the retirement sweep lapses the stuck session
    itself, and 'lapsed' is terminal — excluded by the predicates directly, not
    only by the id-bound."""
    mid = _member("freeze-lapsed@x.com")
    stuck = _ballot(today_et() - datetime.timedelta(days=13), mid, ["AAPL"])

    assert voting.member_in_closed_unexecuted_session(mid) is True
    assert voting.member_has_live_ballot(mid) is True

    executor._retire_stale_sessions()

    assert _sess_status(stuck["id"]) == "lapsed"
    assert voting.member_in_closed_unexecuted_session(mid) is False
    assert voting.member_has_live_ballot(mid) is False
