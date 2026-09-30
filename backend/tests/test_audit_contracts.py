"""Audit-batch contracts that nothing else pins.

Seven behaviours the fix batch introduced, each of which is silent when it
regresses — no error, no alert, just a wrong number on a member's screen or a
cycle that never finishes:

  1. 'lapsed' is a DECIDED status. A retired stuck cycle decided a basket and
     may have traded part of it, so every member-facing, published-series and
     demo-broker query counts it — and `_status_display` renames it, because
     the word is internal bookkeeping the app's status union does not carry.
     The ops console is the one reader that still sees the raw word.
  2. reconcile's fuzzy match is narrowed to ±2h AND the same US/Eastern date.
     The old 20h window let a row bind to the NEXT day's order of the same
     symbol+side — a cross-session mis-promotion that double-counts the ledger.
  3. The A4 leftover loop: a NON-basket errored buy (a DUSTFIX repair, an
     n_eff-dropped name) has no plan row to re-place it, so the row is promoted
     in place when the broker shows the order, retired 'superseded' when the
     broker is read and does not, and left ALONE when the read fails.
  4. W14: the same member frozen out by an unquotable holding on two
     consecutive TRADING runs pages the owner.
  5. The three front-door guards: an over-commit allocation is 422, and
     deleting the account or disconnecting Robinhood mid-execution is 409 —
     each of which must ALLOW the action the moment the freeze does not apply.
  6. _RESIZE_MIN_FRAC: the sell leg's round-trip band. 0.05 churned a full
     position every cycle to close cents of drift; 0.15 does not.
  7. The executed flip: the kill switch must not strand the members it skipped
     (session stays 'closed'), and a DURABLE strand hold keeps the session open
     on the NEXT run — the run that skips the stranded member at the very top
     and therefore shows no strand in its own summary at all.

Run from backend/:  .venv/bin/python -m pytest tests/test_audit_contracts.py -q
(throwaway temp DB shared with the rest of the suite; only the broker surface
is mocked. Trade dates 2095-* and 2096-* are unique to this module — sessions
are UNIQUE on trade_date across the whole run.)
"""
import contextlib
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
from fastapi.testclient import TestClient  # noqa: E402

from app.calendar_us import ET  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_get, kv_set, set_armed  # noqa: E402
from app.main import app  # noqa: E402
from app.robinhood import demo as demo_client  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import (  # noqa: E402
    alloc, executor, performance, reconcile, voting, watchdog,
)

client = TestClient(app)   # no context manager → no startup event (init_db below)

# Rows this module created, so teardown drops exactly those (the suite shares
# one temp DB and later modules sweep every member and session).
_MEMBERS: list[int] = []
_SESSIONS: list[int] = []
_REPORTS: list[int] = []

_ip_counter = [0]


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    with db() as conn:
        for mid in _MEMBERS:
            conn.execute("DELETE FROM executions WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM votes WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM account_snapshots WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
        for sid in _SESSIONS:
            row = conn.execute("SELECT trade_date FROM vote_sessions WHERE id=?",
                               (sid,)).fetchone()
            if row:     # every executor.run() leaves a report, keyed by trade date
                conn.execute("DELETE FROM run_reports WHERE trade_date=?",
                             (row["trade_date"],))
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))
            conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"strand_hold:{sid}:%",))
        for rid in _REPORTS:
            conn.execute("DELETE FROM run_reports WHERE id=?", (rid,))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The A5 retry sleeps 2s between attempts — skip that in tests."""
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _stub_dust(monkeypatch):
    """The per-member crumb sweep is not under test and would place its own $1
    repairs on the armed runs below."""
    monkeypatch.setattr(executor, "dust_cleanup",
                        lambda mid, dry_run=True, session_id=None: {"ok": True, "orders": []})


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.91.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _member(email: str, *, opted_in: int = 1, alloc_type: str = "dollar",
            alloc_value: float = 50.0, alloc_applied: float | None = None,
            connect: bool = True, rh_status: str = "connected",
            role: str = "member", max_orders: int = 25) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,0,?)",
            (email, email.split("@")[0], role, hash_password("contract-pass-11"), now, now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,?,0,0,?,?,?,?)",
            (mid, opted_in, max_orders, alloc_type, alloc_value, now))
        conn.execute("UPDATE member_settings SET compound=1, alloc_applied=?, slice_carry=0 "
                     "WHERE member_id=?", (alloc_applied, mid))
        if connect:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
                "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?,?)",
                (mid, f"ACC{mid}", b"enc", now, now, rh_status))
    _MEMBERS.append(mid)
    return mid


def _token(mid: int) -> str:
    return make_session(mid, 1)   # members.session_version defaults to 1


def _session_row(trade_date: str, status: str, picks: list[str] | None = None) -> int:
    """A vote_sessions row inserted directly (picks_json = the decided basket)."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,closed_at,"
            "top_n,picks_json) VALUES(?,?,?,?,?,?,?)",
            (trade_date, status, now - 7200, now - 3600,
             None if status == "open" else now - 3600, 3,
             json.dumps([{"symbol": s} for s in picks]) if picks is not None else None))
        sid = cur.lastrowid
    _SESSIONS.append(sid)
    return sid


def _vote(sid: int, mid: int, symbols: list[str]) -> None:
    now = int(time.time())
    with db() as conn:
        conn.executemany(
            "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) VALUES(?,?,?,?,?)",
            [(sid, mid, s, 1.0, now) for s in symbols])


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    """A real closed session whose basket is exactly `picks`, alphabetically.

    One voter weights every pick equally, so the tally's tiebreak orders the
    basket by SYMBOL. TOP_N is widened first — the module default of 3 would
    truncate a longer basket."""
    object.__setattr__(settings, "top_n", max(settings.top_n, len(picks)))
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


def _sess_status(sid: int) -> str:
    with db() as conn:
        return conn.execute("SELECT status FROM vote_sessions WHERE id=?",
                            (sid,)).fetchone()["status"]


def _exec_rows(sid: int, mid: int, symbol: str, side: str = "buy") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM executions WHERE session_id=? AND member_id=? AND symbol=? AND side=?",
            (sid, mid, symbol, side)).fetchall()
    return [dict(r) for r in rows]


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _seed_holding(mid: int, symbol: str, qty: float, sid: int) -> None:
    """A prior cycle's placed club buy — the club-ledger side of a holding.
    Pair with a matching live position or the sell cap reads it as zero.

    Backdated a week: the sell leg's PDT guard refuses to sell any name whose
    buy row was created after the previous trading day's 16:00 ET, and _record
    stamps created_at=now, so a holding seeded at `now` reads as bought this
    morning and can never be exited."""
    executor._record(sid, mid, symbol, round(qty, 2), f"hold-{sid}-{mid}-{symbol}",
                     "placed", place={"_concord_est_qty": qty})
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND member_id=? "
                     "AND symbol=?", (int(time.time()) - 7 * 86400, sid, mid, symbol))


@contextlib.contextmanager
def _armed():
    """Run for real: the paths under test (the A4 leftover loop, the executed
    flip, the resize band on an armed sell leg) are all gated on will_place, so
    a dry run proves nothing. Basket floors are waived — these baskets are sized
    for exact arithmetic, not breadth."""
    prev_mode = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 1)
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        yield
    finally:
        object.__setattr__(settings, "execution_mode", prev_mode)
        set_armed(False)


class FakeClient:
    """Scripted RobinhoodClient double; records every call it receives."""

    def __init__(self, *, quotes=None, positions=None, orders=None, orders_exc=None,
                 buying_power=1000.0, total_value=1000.0):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.orders_list = orders or []
        self.orders_exc = orders_exc
        self.buying_power = buying_power
        self.total_value = total_value

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

    def get_positions(self, acct):
        self.calls.append(("get_positions", acct))
        return {"data": {"positions": [
            {"symbol": s, "type": "long", "quantity": str(q)} for s, q in self.positions_list]}}

    def get_quotes(self, symbols):
        self.calls.append(("get_quotes", tuple(symbols)))
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes_map[s])}}
            for s in symbols if s in self.quotes_map]}}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        self.calls.append(("get_equity_orders", acct, placed_agent))
        if self.orders_exc is not None:
            raise self.orders_exc
        return {"data": {"orders": list(self.orders_list), "next": None}}

    def review_market_buy(self, acct, symbol, amt):
        self.calls.append(("review_buy", acct, symbol, amt))
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, acct, symbol, amt, ref_id=None):
        self.calls.append(("place_buy", acct, symbol, amt, ref_id))
        return {"data": {"order": {}}}

    def review_market_sell(self, acct, symbol, qty):
        self.calls.append(("review_sell", acct, symbol, qty))
        return {"data": {"order_checks": {}}}

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.calls.append(("place_sell", acct, symbol, qty, ref_id))
        return {"data": {"order": {}}}

    def trades_for(self, kind: str, acct: str, symbol: str | None = None) -> list[tuple]:
        return [c for c in self.calls
                if c[0] == kind and c[1] == acct and (symbol is None or c[2] == symbol)]


def _use_client(monkeypatch, c: FakeClient) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda mid: c)


def _no_reconcile(monkeypatch) -> None:
    """The A4 leftover loop is the SECOND line of defence: reconcile_all runs
    first on every live run and, when it can read the feed, claims the same
    broker order itself. Stub it out so these assertions are about the loop —
    which is what runs for the passes where reconcile could not resolve the row.
    """
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=True: {"ok": True})


# ═══ 1. 'lapsed' is a decided status ═════════════════════════════════════════
#
# _retire_stale_sessions flips a stuck 'closed' cycle to 'lapsed'. That cycle
# DECIDED a basket and may have traded part of it, so dropping it from the
# decided-session filters silently rewrites the club's record: the published
# performance series loses a day of the decisions index, and the member's own
# history shows the cycle with no basket at all.

def test_lapsed_cycle_stays_in_the_published_performance_series():
    """decided_cycles feeds the VERIFIED decisions index on the public site.
    A lapsed cycle held a basket for that day like any other — excluding it
    re-prices the published return."""
    sid = _session_row("2095-11-01", "lapsed", ["AAPL", "MSFT"])
    cycles = performance.decided_cycles()
    mine = [c for c in cycles if c["trade_date"] == "2095-11-01"]
    assert mine == [{"trade_date": "2095-11-01", "symbols": ["AAPL", "MSFT"]}], cycles[-3:]
    assert _sess_status(sid) == "lapsed"


def test_lapsed_cycle_is_in_club_history_and_never_says_lapsed():
    """The shared club record must carry the cycle, and `status_display` must
    read 'executed' — 'lapsed' is internal bookkeeping the app's status union
    does not carry, so it would render as an unknown state."""
    sid = _session_row("2095-11-02", "lapsed", ["AAPL", "NVDA"])
    row = next((h for h in voting.club_history(limit=60) if h["id"] == sid), None)
    assert row is not None, "a lapsed cycle is part of the club's record"
    assert row["basket"] == ["AAPL", "NVDA"]
    assert row["status_display"] == "executed"


def test_status_display_renames_lapsed_even_when_it_is_the_latest_decided():
    """The 'closed' rename is conditional (only a cycle that is NOT the latest
    decided reads as executed). The 'lapsed' rename is unconditional — a lapsed
    cycle is finished business whether or not it is the most recent one."""
    s = {"id": 424242, "status": "lapsed"}
    assert voting._status_display(s, latest_decided_id=424242) == "executed"
    assert voting._status_display(s, latest_decided_id=999999) == "executed"
    # and the conditional branch it must not have broken
    assert voting._status_display({"id": 424242, "status": "closed"},
                                  latest_decided_id=424242) == "closed"


def test_member_vote_history_shows_a_lapsed_cycles_basket():
    """The member voted in this cycle; a decided basket exists. Gating on
    ('closed','executed') alone blanked the basket AND the hits, so the member's
    own record showed them voting into nothing."""
    mid = _member("lapsed-hist@contract.test", opted_in=0)
    sid = _session_row("2095-11-03", "lapsed", ["AAPL", "MSFT"])
    _vote(sid, mid, ["AAPL", "TSLA"])

    row = next(h for h in voting.member_vote_history(mid, limit=60) if h["id"] == sid)
    assert row["basket"] == ["AAPL", "MSFT"]
    assert row["hits"] == ["AAPL"]
    assert row["status_display"] == "executed"


def test_vote_results_falls_back_to_a_lapsed_basket(monkeypatch):
    """GET /api/vote/results shows `last_decided` so the Vote page is never
    basketless. The newest decided cycle here IS the lapsed one."""
    mid = _member("lapsed-results@contract.test", opted_in=0)
    _session_row("2095-11-04", "lapsed", ["AAPL", "MSFT"])
    _session_row("2095-11-05", "open")       # today's ballot, nothing decided yet

    r = client.get("/api/vote/results", headers=_hdrs(_token(mid)))
    assert r.status_code == 200, r.text
    last = r.json()["last_decided"]
    assert last["trade_date"] == "2095-11-04"
    assert [p["symbol"] for p in last["picks"]] == ["AAPL", "MSFT"]


def test_admin_cycles_lists_a_lapsed_cycle_with_its_raw_status():
    """The ops console is the one reader that must still see the WORD: the
    member rename hides an internal state members have no vocabulary for, while
    the operator needs to know this cycle was retired rather than executed. So
    the row is listed (dropping it hid the very cycles an operator goes looking
    for) and carries both spellings."""
    admin = _member("lapsed-admin@contract.test", opted_in=0, role="owner")
    sid = _session_row("2095-11-06", "lapsed", ["AAPL", "MSFT"])

    r = client.get("/api/admin/cycles?limit=60", headers=_hdrs(_token(admin)))
    assert r.status_code == 200, r.text
    row = next((c for c in r.json()["cycles"] if c["id"] == sid), None)
    assert row is not None, "a retired cycle is exactly what an operator audits"
    assert row["status"] == "lapsed" and row["status_display"] == "executed"
    assert row["basket"] == ["AAPL", "MSFT"]


def test_demo_account_buys_in_on_a_lapsed_cycle():
    """DEMO_MODE's stand-in broker holds cash until the first cycle DECIDES
    after the stake was committed. A lapsed cycle decided a basket, so it is a
    buy-in — excluding it left the demo member staring at an all-cash account
    that never invests, which is the whole demo."""
    mid = _member("lapsed-demo@contract.test", opted_in=0, alloc_type="dollar",
                  alloc_value=400.0)
    committed = int(time.time()) + 86400      # later than every other session's close
    with db() as conn:
        conn.execute("UPDATE member_settings SET updated_at=? WHERE member_id=?",
                     (committed, mid))
    demo = demo_client.DemoClient(mid)
    assert demo._stake_fraction() == 0.0, "nothing has decided since the commit"

    sid = _session_row("2095-11-07", "lapsed", ["AAPL"])
    with db() as conn:
        conn.execute("UPDATE vote_sessions SET closed_at=? WHERE id=?", (committed + 3600, sid))

    assert demo._stake_fraction() == pytest.approx(0.4)


# ═══ 2. reconcile's fuzzy match: ±2h AND the same ET date ════════════════════
#
# The agentic API returns ref_id=null on every order, so an un-bound row is
# matched by symbol + side + closest created-time. A 20h window with no date
# bind reached across the cadence: yesterday's row and today's order of the same
# name and side are both "within 20h", so the row promoted against the WRONG
# cycle's order and the ledger counted the same shares twice.

def _ts_et(y: int, mo: int, d: int, h: int, mi: int) -> float:
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


def _order(symbol: str, side: str, ts: float, qty: float, *, oid: str | None = None) -> dict:
    o = {"symbol": symbol, "side": side, "created_at": ts, "state": "filled",
         "cumulative_quantity": str(qty)}
    if oid:
        o["id"] = oid
    return o


def _placed_row(sid: int, mid: int, symbol: str, est_qty: float, ts: float,
                side: str = "buy") -> int:
    """A placed club order still carrying its ESTIMATE — the shape reconcile
    exists to rewrite. No broker order id, so it takes the fuzzy path."""
    executor._record(sid, mid, symbol, round(est_qty, 2), f"fuzzy-{sid}-{mid}-{symbol}-{side}",
                     "placed", place={"_concord_est_qty": est_qty}, side=side)
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND member_id=? "
                     "AND symbol=? AND side=?", (int(ts), sid, mid, symbol, side))
        return conn.execute("SELECT id FROM executions WHERE session_id=? AND member_id=? "
                            "AND symbol=? AND side=?", (sid, mid, symbol, side)).fetchone()["id"]


def _filled_qty(row_id: int) -> float | None:
    with db() as conn:
        raw = conn.execute("SELECT place_json FROM executions WHERE id=?",
                           (row_id,)).fetchone()["place_json"]
    pj = json.loads(raw or "{}")
    q = ((pj.get("data") or {}).get("order") or {}).get("cumulative_quantity")
    return None if q is None else float(q)


def test_same_date_order_seconds_away_still_reconciles():
    """The narrowing must not break the case it exists for: our row is stamped
    seconds from the broker order that caused it, on the same ET date."""
    mid = _member("fuzzy-near@contract.test", opted_in=0)
    sid = _session_row("2096-02-02", "closed", ["AAPL"])
    ts = _ts_et(2096, 2, 2, 10, 15)
    row_id = _placed_row(sid, mid, "AAPL", 3.0, ts)
    feed = FakeClient(orders=[_order("AAPL", "buy", ts + 30, 2.987654)])

    res = reconcile.reconcile_fills(mid, client=feed, account_number=f"ACC{mid}", session_id=sid)

    assert res["ok"] is True
    assert [e["symbol"] for e in res["reconciled"]] == ["AAPL"]
    assert _filled_qty(row_id) == pytest.approx(2.987654)


def test_next_day_order_of_the_same_name_is_not_claimed():
    """THE CROSS-SESSION MIS-PROMOTION. Yesterday's 15:00 row and today's 09:35
    order are 18h35m apart — inside the old 20h window, and the same symbol and
    side. Binding them rewrites yesterday's fill from TODAY's order, so the
    club's ledger counts today's shares against both cycles."""
    mid = _member("fuzzy-nextday@contract.test", opted_in=0)
    sid = _session_row("2096-02-03", "closed", ["AAPL"])
    row_ts = _ts_et(2096, 2, 3, 15, 0)
    row_id = _placed_row(sid, mid, "AAPL", 3.0, row_ts)
    next_day = _ts_et(2096, 2, 4, 9, 35)
    assert 0 < next_day - row_ts < 20 * 3600, "the old window would have spanned this"
    feed = FakeClient(orders=[_order("AAPL", "buy", next_day, 99.0)])

    res = reconcile.reconcile_fills(mid, client=feed, account_number=f"ACC{mid}", session_id=sid)

    assert res["ok"] is True
    assert res["reconciled"] == [] and res["promoted"] == []
    assert res["unmatched"] == 1
    assert _filled_qty(row_id) is None, "the estimate stands — nothing proved it wrong"


def test_a_same_date_order_hours_away_is_not_claimed():
    """And the tolerance half, isolated. One ET day can legitimately carry two
    agentic orders for the same name and side — the cycle's buy at the open and
    a crumb repair later on. A row is stamped SECONDS from the order it placed,
    so when that order is missing from the feed the row must go unmatched, not
    reach five hours down the day and rewrite its fill from someone else's."""
    mid = _member("fuzzy-sameday@contract.test", opted_in=0)
    sid = _session_row("2096-02-04", "closed", ["AAPL"])
    row_ts = _ts_et(2096, 2, 4, 9, 35)
    row_id = _placed_row(sid, mid, "AAPL", 3.0, row_ts)
    later = _ts_et(2096, 2, 4, 15, 0)
    assert 2 * 3600 < later - row_ts < 20 * 3600, "same ET date, outside ±2h"
    feed = FakeClient(orders=[_order("AAPL", "buy", later, 88.0)])

    res = reconcile.reconcile_fills(mid, client=feed, account_number=f"ACC{mid}", session_id=sid)

    assert res["reconciled"] == [] and res["unmatched"] == 1
    assert _filled_qty(row_id) is None


def test_the_date_bind_holds_even_inside_the_two_hour_window():
    """The two halves of the narrowing are independent. An order one hour away
    but on the NEXT ET calendar date is inside ±2h and still must not bind —
    the cadence runs once a day, so a date change means a different cycle."""
    mid = _member("fuzzy-midnight@contract.test", opted_in=0)
    sid = _session_row("2096-02-05", "closed", ["AAPL"])
    row_ts = _ts_et(2096, 2, 5, 23, 30)
    row_id = _placed_row(sid, mid, "AAPL", 3.0, row_ts)
    over_midnight = _ts_et(2096, 2, 6, 0, 30)
    assert over_midnight - row_ts == pytest.approx(3600), "well inside ±2h"
    feed = FakeClient(orders=[_order("AAPL", "buy", over_midnight, 77.0)])

    res = reconcile.reconcile_fills(mid, client=feed, account_number=f"ACC{mid}", session_id=sid)

    assert res["reconciled"] == [] and res["unmatched"] == 1
    assert _filled_qty(row_id) is None


# ═══ 3. the A4 leftover loop (non-basket errored buys) ═══════════════════════
#
# A buy that raised AFTER the broker may have accepted it is recorded 'error'.
# For a name in THIS cycle's basket the buy leg re-visits it. A NON-basket name
# — a DUSTFIX crumb repair, a name the n_eff clamp dropped — has no plan row at
# all, so without this loop its error row survives every run untouched: never
# counted as a holding (the shares are invisible to the ledger) and never
# retired (so it blocks the session's executed flip forever).

def test_leftover_errored_buy_the_broker_shows_is_promoted_in_place(monkeypatch):
    """The order landed. The row must become 'placed' so club_shares counts the
    shares — and must never be re-placed (that is a duplicate buy)."""
    _no_reconcile(monkeypatch)
    mid = _member("a4-left-promote@contract.test", alloc_value=40.0, alloc_applied=25.0)
    closed = _closed_session(datetime.date(2095, 12, 1), mid, ["AAPL"])
    sid = closed["id"]
    # MSFT is NOT in this cycle's basket: a prior run's repair that errored.
    executor._record(sid, mid, "MSFT", 25.0, "a4-left-1", "error",
                     error="place: connection reset")
    feed = FakeClient(quotes={"AAPL": 10.0, "MSFT": 10.0},
                      orders=[{"symbol": "MSFT", "side": "buy", "created_at": time.time(),
                               "dollar_based_amount": {"amount": "25.00", "currency_code": "USD"}, "state": "filled",
                               "cumulative_quantity": "2.5"}])
    _use_client(monkeypatch, feed)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    rows = _exec_rows(sid, mid, "MSFT", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "placed", rows
    assert json.loads(rows[0]["place_json"])["_concord_recovered"] is True
    assert not feed.trades_for("place_buy", f"ACC{mid}", "MSFT"), "never re-placed"
    assert not feed.trades_for("review_buy", f"ACC{mid}", "MSFT")


def test_leftover_errored_buy_verified_absent_is_superseded(monkeypatch):
    """The feed READ SUCCEEDED and shows no such order, so the buy never landed.
    The row is retired terminal — leaving it 'error' would hold the session's
    executed flip open for a cycle that is finished."""
    _no_reconcile(monkeypatch)
    mid = _member("a4-left-absent@contract.test", alloc_value=40.0, alloc_applied=25.0)
    closed = _closed_session(datetime.date(2095, 12, 2), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "MSFT", 25.0, "a4-left-2", "error", error="place: boom")
    feed = FakeClient(quotes={"AAPL": 10.0}, orders=[])
    _use_client(monkeypatch, feed)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    rows = _exec_rows(sid, mid, "MSFT", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "superseded", rows
    assert not feed.trades_for("place_buy", f"ACC{mid}", "MSFT"), \
        "a verified-absent NON-basket buy is retired, never re-placed"
    assert _sess_status(sid) == "executed", "and the cycle can finish"


def test_leftover_errored_buy_survives_an_unreadable_feed(monkeypatch):
    """Absence is only evidence when the feed was actually read. A failed fetch
    must leave the row exactly as it is — superseding here would erase the only
    record of an order the broker may hold."""
    _no_reconcile(monkeypatch)
    mid = _member("a4-left-blind@contract.test", alloc_value=40.0, alloc_applied=25.0)
    closed = _closed_session(datetime.date(2095, 12, 3), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "MSFT", 25.0, "a4-left-3", "error", error="place: boom")
    feed = FakeClient(quotes={"AAPL": 10.0}, orders_exc=RuntimeError("MCP HTTP 500"))
    _use_client(monkeypatch, feed)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    rows = _exec_rows(sid, mid, "MSFT", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "error", rows
    assert rows[0]["error"] == "place: boom", "the evidence text survives verbatim"
    assert _sess_status(sid) == "closed", "an unresolved error row holds the flip"


# ═══ 4. W14 — a member frozen out by an unquotable holding ══════════════════

def _report(trade_date: str, members: list[dict], started_at: int) -> int:
    with db() as conn:
        cur = conn.execute(
            'INSERT INTO run_reports(trade_date,phase,mode,"trigger",will_place,'
            "started_at,finished_at,summary_json) VALUES(?,?,?,?,1,?,?,?)",
            (trade_date, "both", "TEST", "schedule", started_at, started_at,
             json.dumps({"members": members})))
        rid = cur.lastrowid
    _REPORTS.append(rid)
    return rid


def test_w14_alerts_when_the_same_member_is_frozen_two_runs_running():
    """A club-held name returning no quote fails the member's valuation closed
    every run — nothing trades for them until the owner marks it written off,
    and nothing else says so."""
    mid = _member("w14-frozen@contract.test", opted_in=0)
    other = _member("w14-fine@contract.test", opted_in=0)
    now = int(time.time())
    _report("2096-03-02", [{"member_id": mid, "skip_reason": "unquotable_holding"},
                           {"member_id": other}], now - 86400)
    _report("2096-03-03", [{"member_id": mid, "skip_reason": "unquotable_holding"},
                           {"member_id": other}], now)

    finds = watchdog._check_unquotable_holding()
    hit = next(f for f in finds if f["key"] == f"unquotable-holding:{mid}")
    assert hit["severity"] == "warn"
    assert "w14-frozen" in hit["message"] and "unquotable holding" in hit["message"]
    assert not any(f["key"] == f"unquotable-holding:{other}" for f in finds)
    # and the sweep actually runs it — a check absent from _CHECKS alerts nobody
    assert ("unquotable-holding", watchdog._check_unquotable_holding) in watchdog._CHECKS


def test_w14_stays_quiet_for_a_single_transient_run():
    """One run is a quote hiccup, not a frozen member. Two CONSECUTIVE runs is
    the condition — alerting on one would page the owner on every blip."""
    mid = _member("w14-blip@contract.test", opted_in=0)
    now = int(time.time())
    _report("2096-03-04", [{"member_id": mid}], now - 86400)
    _report("2096-03-05", [{"member_id": mid, "skip_reason": "unquotable_holding"}], now)

    assert not any(f["key"] == f"unquotable-holding:{mid}"
                   for f in watchdog._check_unquotable_holding())


def test_w14_an_idle_day_between_two_frozen_runs_does_not_silence_it():
    """run_reports carries a row per INVOCATION — idle days included, and an
    idle report has no members at all. Counting rows rather than trading days
    let one idle day wedged between two frozen days silence the alert for good.
    """
    mid = _member("w14-idle@contract.test", opted_in=0)
    now = int(time.time())
    _report("2096-03-06", [{"member_id": mid, "skip_reason": "unquotable_holding"}], now - 172800)
    _report("2096-03-07", [], now - 86400)              # idle day: no members
    _report("2096-03-08", [{"member_id": mid, "skip_reason": "unquotable_holding"}], now)

    assert any(f["key"] == f"unquotable-holding:{mid}"
               for f in watchdog._check_unquotable_holding())


# ═══ 5. the front-door guards ═══════════════════════════════════════════════

def _freeze_member(mid: int, trade_date: str) -> int:
    """Put the member inside the close→execute window: a 'closed' session,
    NEWER than the newest executed one, that they voted in."""
    sid = _session_row(trade_date, "closed", ["AAPL"])
    _vote(sid, mid, ["AAPL"])
    return sid


def test_set_allocation_rejects_a_stake_larger_than_the_account(monkeypatch):
    """The club can only deploy what the account holds. A $5,000 stake on a
    $1,000 account is a number that can never be honoured — refuse it at the
    front door instead of letting the executor silently clamp it forever."""
    mid = _member("guard-overcommit@contract.test")
    monkeypatch.setattr(alloc, "member_balance",
                        lambda m: {"total_value": 1000.0, "cash": 0.0, "buying_power": 0.0})

    r = client.put("/api/me/allocation", headers=_hdrs(_token(mid)),
                   json={"type": "dollar", "value": 5000})
    assert r.status_code == 422, r.text
    assert "more than this account holds" in r.json()["detail"]
    with db() as conn:
        assert conn.execute("SELECT alloc_value FROM member_settings WHERE member_id=?",
                            (mid,)).fetchone()["alloc_value"] == 50.0


def test_set_allocation_allows_a_stake_the_account_covers(monkeypatch):
    """The cap is a CAP, not a ban: a stake inside the balance goes through, and
    a percent stake is never measured against dollars at all."""
    mid = _member("guard-inbounds@contract.test")
    monkeypatch.setattr(alloc, "member_balance",
                        lambda m: {"total_value": 1000.0, "cash": 0.0, "buying_power": 0.0})

    assert client.put("/api/me/allocation", headers=_hdrs(_token(mid)),
                      json={"type": "dollar", "value": 900}).status_code == 200

    # A percent is a share OF the balance, so it can never over-commit — even
    # 100% of an account far smaller than the number written in the field.
    monkeypatch.setattr(alloc, "member_balance",
                        lambda m: {"total_value": 50.0, "cash": 0.0, "buying_power": 0.0})
    assert client.put("/api/me/allocation", headers=_hdrs(_token(mid)),
                      json={"type": "percent", "value": 100}).status_code == 200
    with db() as conn:
        row = conn.execute("SELECT alloc_type, alloc_value FROM member_settings "
                           "WHERE member_id=?", (mid,)).fetchone()
    assert (row["alloc_type"], row["alloc_value"]) == ("percent", 100.0)


def test_set_allocation_fails_open_when_the_balance_is_unreadable(monkeypatch):
    """A broker hiccup must not lock a member out of their own settings — the
    executor-side capacity clamp is the real guarantee, this is only the cheap
    front-door check."""
    mid = _member("guard-blindbalance@contract.test")
    monkeypatch.setattr(alloc, "member_balance", lambda m: None)

    r = client.put("/api/me/allocation", headers=_hdrs(_token(mid)),
                   json={"type": "dollar", "value": 5000})
    assert r.status_code == 200, r.text


def test_delete_account_is_refused_mid_execution():
    """Between close and execute the member's frozen vote weight is being turned
    into real orders on every account. Deleting there defects from a basket they
    shaped and funds none of it — the cost lands on whoever stayed in."""
    mid = _member("guard-delete@contract.test")
    _freeze_member(mid, "2096-04-01")

    r = client.request("DELETE", "/api/me", headers=_hdrs(_token(mid)),
                       json={"confirm": "DELETE"})
    assert r.status_code == 409, r.text
    assert "basket is executing" in r.json()["detail"]
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM members WHERE id=?",
                            (mid,)).fetchone()["c"] == 1


def test_delete_account_goes_through_outside_the_window():
    """Nothing is executing, so the guard must not apply — self-service deletion
    is a terms §7 right, not something a stale session may block."""
    mid = _member("guard-delete-ok@contract.test")

    r = client.request("DELETE", "/api/me", headers=_hdrs(_token(mid)),
                       json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    with db() as conn:
        assert conn.execute("SELECT opted_in FROM member_settings WHERE member_id=?",
                            (mid,)).fetchone()["opted_in"] == 0


def test_robinhood_disconnect_is_refused_mid_execution():
    """Same window, same money: disconnecting mid-execution defunds a basket the
    member helped choose."""
    mid = _member("guard-disc@contract.test")
    _freeze_member(mid, "2096-04-02")

    r = client.post("/api/oauth/robinhood/disconnect", headers=_hdrs(_token(mid)))
    assert r.status_code == 409, r.text
    assert "basket is executing" in r.json()["detail"]
    with db() as conn:
        assert conn.execute("SELECT status FROM rh_accounts WHERE member_id=?",
                            (mid,)).fetchone()["status"] == "connected"


def test_robinhood_disconnect_goes_through_outside_the_window():
    mid = _member("guard-disc-ok@contract.test")

    r = client.post("/api/oauth/robinhood/disconnect", headers=_hdrs(_token(mid)))
    assert r.status_code == 200, r.text
    with db() as conn:
        assert conn.execute("SELECT status FROM rh_accounts WHERE member_id=?",
                            (mid,)).fetchone()["status"] == "revoked"


# ═══ 6. _RESIZE_MIN_FRAC — the round-trip band ══════════════════════════════
#
# A position under target whose top-up is below the broker's $1 minimum can only
# be corrected by exiting it whole and re-entering at size. That round trip pays
# the spread twice, so the band that triggers it has to be wide enough that
# ordinary daily drift does not churn the position every single cycle (0.05 did
# exactly that) and tight enough that a small stake can still track the basket.

def test_a_six_percent_drift_is_not_round_tripped(monkeypatch):
    """$9.40 held against a $10.00 target: $0.60 short, 6% — under the band. It
    cannot be topped up (under $1) and must NOT be sold; the dollars carry."""
    # budget = club_value 9.40 + (30.60 - 20.00) = 20.00 over two names = $10.00
    mid = _member("resize-small@contract.test", alloc_value=20.0, alloc_applied=9.40)
    picks = ["AAPL", "ABT"]
    closed = _closed_session(datetime.date(2095, 10, 1), mid, picks)
    _seed_holding(mid, "AAPL", 9.40, 97001)
    feed = FakeClient(quotes={s: 1.0 for s in picks}, positions=[("AAPL", "9.40")],
                      buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, feed)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    orders = _mrow(summary, mid)["orders"]
    assert _mrow(summary, mid)["per_pick"] == 10.0
    assert not [o for o in orders if o["symbol"] == "AAPL" and o["side"] == "sell"], \
        "cent-level drift must not pay the spread twice every cycle"
    assert not feed.trades_for("place_sell", f"ACC{mid}", "AAPL")


def test_a_twenty_percent_drift_is_resized(monkeypatch):
    """The other edge. $1.02 held of a $1 stock against a $1.25 target is $0.23
    short — 18.4%, over the band, and under the $1 minimum order, so the round
    trip is the ONLY convergence path there is. This is the shape the band
    exists for: on a per-name target barely over $1, almost every drift is
    unplaceable, and a band that never fires freezes the position forever."""
    # budget = club_value 1.02 + (1.25 - 1.02) = 1.25 over one name = $1.25
    mid = _member("resize-big@contract.test", alloc_value=1.25, alloc_applied=1.02)
    picks = ["AAPL"]
    closed = _closed_session(datetime.date(2095, 10, 2), mid, picks)
    _seed_holding(mid, "AAPL", 1.02, 97002)
    feed = FakeClient(quotes={"AAPL": 1.0}, positions=[("AAPL", "1.02")],
                      buying_power=1.25, total_value=1.25)
    _use_client(monkeypatch, feed)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    orders = _mrow(summary, mid)["orders"]
    sell = next(o for o in orders if o["symbol"] == "AAPL" and o["side"] == "sell")
    assert sell["status"] == "placed" and "resize" in (sell.get("note") or ""), sell
    buy = next(o for o in orders if o["symbol"] == "AAPL" and o["side"] == "buy")
    assert buy["status"] == "placed", "and re-entered at the right size"


# ═══ 7. the executed flip: halt and the durable strand hold ═════════════════

def test_a_halted_run_leaves_the_session_closed(monkeypatch):
    """The kill switch cuts the member loop short. Every member it did not reach
    still has a half-finished cycle, so flipping to 'executed' would hide the
    session from the next run and there would be no way to finish it once the
    operator lifts the halt."""
    mid = _member("halt-open@contract.test", alloc_value=20.0, alloc_applied=20.0)
    closed = _closed_session(datetime.date(2095, 9, 1), mid, ["AAPL"])
    sid = closed["id"]
    feed = FakeClient(quotes={"AAPL": 1.0}, buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, feed)

    kv_set("halt", "1")
    try:
        with _armed():
            summary = executor.run(closed, force_dry_run=False, phase="buy")
    finally:
        kv_set("halt", "0")

    assert summary["halted"] is True
    assert "halted mid-run" in summary.get("executed_deferred", "")
    assert _sess_status(sid) == "closed", "a halt must not strand the members it skipped"
    assert not feed.trades_for("place_buy", f"ACC{mid}")


def test_a_durable_strand_hold_keeps_the_session_open_on_the_next_run(monkeypatch):
    """THE RUN THAT SHOWS NO STRAND. A terminal auth failure marks the
    connection 'error', so the NEXT run skips the member at the very top —
    its own summary carries no strand at all. Scanning only that summary
    flipped the session to 'executed' and the proceeds already sold could never
    be redeployed. The hold is a kv memo precisely so it outlives the run."""
    mid = _member("strand-durable@contract.test", alloc_value=20.0, alloc_applied=20.0,
                  rh_status="error")
    closed = _closed_session(datetime.date(2095, 9, 2), mid, ["AAPL"])
    sid = closed["id"]
    executor._hold_strand(sid, mid, will_place=True)     # what last run's mid-pass strand left
    assert kv_get(f"strand_hold:{sid}:{mid}") == "1"
    feed = FakeClient(quotes={"AAPL": 1.0}, buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, feed)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(summary, mid)["skip_reason"] == "rh_error_stranded"
    assert not _mrow(summary, mid).get("auth_stranded"), \
        "this run never even reached the member's orders — the hold is the only evidence"
    assert f"stranded mid-pass" in summary.get("executed_deferred", "")
    assert _sess_status(sid) == "closed"

    # and releasing the hold lets the very next run finish the cycle
    executor._clear_strand(sid, mid)
    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")
    assert _sess_status(sid) == "executed"
