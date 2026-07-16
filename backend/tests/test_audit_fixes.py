"""Tests for the 2026-07-15 audit fixes.

Focus: fill RECONCILIATION (the "not all shares get sold" root cause), plus the
sign-aware club P&L, the calendar-coverage guard, the +inf allocation guard, the
human-quorum count, and the vote-weight freeze.

Run from backend/:  python -m pytest tests/test_audit_fixes.py -q
(throwaway temp DB; Robinhood client + vault mocked — no network.)
"""
import datetime
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

from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import alloc, executor, positions, reconcile, voting  # noqa: E402


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    # These test modules share one temp DB (CONCORD_DB_PATH via setdefault), and
    # executor.run() in other modules scans ALL members/executions. Reset the
    # mutable tables so this module (which runs first, alphabetically) doesn't
    # leak members/rows into the others' summaries.
    with db() as conn:
        for t in ("executions", "votes", "bot_picks", "vote_sessions",
                  "voting_power_history", "login_events", "api_keys", "audit_log",
                  "rh_accounts", "member_settings", "members"):
            conn.execute(f"DELETE FROM {t}")


def _member(email, opted_in=1, alloc_value=50.0, connect=True, is_bot=0):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) VALUES(?,?,?,?,?)",
            (email, email.split("@")[0], "member", is_bot, int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (mid, opted_in, 10.0, 500.0, 10, "percent", alloc_value, int(time.time())),
        )
        if connect:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
                "connected_at,last_refresh_at,status) VALUES(?,?,?,?,?,?,?)",
                (mid, f"ACC{mid}", 1, b"enc", int(time.time()), int(time.time()), "connected"),
            )
    return mid


class OrdersClient:
    """Scripted client for reconcile (orders) + club_pnl (positions/quotes)."""

    def __init__(self, orders=None, positions=None, quotes=None):
        self.orders = orders or []
        self.positions = positions or []   # list of (symbol, qty, avg_buy_price)
        self.quotes = quotes or {}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        orders = self.orders
        if placed_agent:   # honour the filter like the real API
            orders = [o for o in orders if o.get("placed_agent", "agentic") == placed_agent]
        return {"data": {"orders": orders, "next": None}}

    def get_positions(self, acct):
        return {"data": {"positions": [
            {"symbol": s, "type": "long", "quantity": str(q), "average_buy_price": str(a)}
            for s, q, a in self.positions
        ]}}

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes[s])}}
            for s in symbols if s in self.quotes
        ]}}


def _use(monkeypatch, client):
    monkeypatch.setattr(vault, "account_number_for", lambda m: f"ACC{m}")
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _order(symbol, side, state, cum, agent="agentic", created=None):
    """A broker order row (note: real RH returns ref_id=null, so we match on
    symbol+side+time among placed_agent='agentic' orders)."""
    return {"symbol": symbol, "side": side, "state": state,
            "cumulative_quantity": str(cum), "quantity": str(cum),
            "placed_agent": agent, "created_at": created or _now_iso()}


# ─── fill reconciliation ─────────────────────────────────────────────────────

def test_reconcile_rewrites_estimate_to_real_fill(monkeypatch):
    """A placed buy on an ESTIMATE (2.5) is corrected to the broker's real fill
    (2.4) via symbol+side+time match, so club_shares becomes fill-based."""
    mid = _member("rec-fill@x.com")
    executor._record(70001, mid, "AAPL", 500.0, "rc-ref-1", "placed",
                     place={"data": {"order": {}}, "_concord_est_qty": 2.5})
    assert positions.club_shares(mid).get("AAPL") == pytest.approx(2.5)   # estimate first
    _use(monkeypatch, OrdersClient(orders=[_order("AAPL", "buy", "filled", "2.4")]))

    res = reconcile.reconcile_fills(mid)

    assert res["changed"] == 1 and len(res["reconciled"]) == 1
    assert positions.club_shares(mid).get("AAPL") == pytest.approx(2.4)   # now fill-based


def test_reconcile_promotes_errored_sell_that_filled(monkeypatch):
    """An errored SELL the broker actually FILLED is promoted to 'placed' so
    club_shares subtracts it (finding #2 — the errored-sell gap A4 left open)."""
    mid = _member("rec-sell@x.com")
    executor._record(70002, mid, "MSFT", 500.0, "b-ref", "placed", place={"_concord_est_qty": 3.0})
    executor._record(70002, mid, "MSFT", None, "s-ref", "error", side="sell", error="place: boom")
    assert positions.club_shares(mid).get("MSFT") == pytest.approx(3.0)   # sell not yet counted
    _use(monkeypatch, OrdersClient(orders=[_order("MSFT", "sell", "filled", "3.0")]))

    res = reconcile.reconcile_fills(mid)

    assert len(res["promoted"]) == 1
    assert positions.club_shares(mid).get("MSFT", 0.0) == pytest.approx(0.0, abs=1e-9)


def test_reconcile_demotes_broker_rejected_placed(monkeypatch):
    """A 'placed' row the broker actually REJECTED is demoted to 'skipped' so
    club_shares stops counting a fill that never happened."""
    mid = _member("rec-reject@x.com")
    executor._record(70003, mid, "NVDA", 100.0, "r-ref", "placed", place={"_concord_est_qty": 1.0})
    assert positions.club_shares(mid).get("NVDA") == pytest.approx(1.0)
    _use(monkeypatch, OrdersClient(orders=[_order("NVDA", "buy", "rejected", "0")]))

    res = reconcile.reconcile_fills(mid)

    assert len(res["demoted"]) == 1
    assert "NVDA" not in positions.club_shares(mid)


def test_reconcile_excludes_personal_orders(monkeypatch):
    """A PERSONAL (placed_agent='user') order of the same symbol is NEVER matched
    — reconciliation can't touch personal shares — so the estimate is kept."""
    mid = _member("rec-personal@x.com")
    executor._record(70004, mid, "TSLA", 250.0, "t-ref", "placed", place={"_concord_est_qty": 1.0})
    _use(monkeypatch, OrdersClient(orders=[_order("TSLA", "buy", "filled", "1.2", agent="user")]))

    res = reconcile.reconcile_fills(mid)

    assert res["changed"] == 0 and res["unmatched"] >= 1
    assert positions.club_shares(mid).get("TSLA") == pytest.approx(1.0)


def test_reconcile_ignores_far_off_time(monkeypatch):
    """An agentic order far outside the time window (a different cycle) is not
    matched to this execution."""
    mid = _member("rec-faroff@x.com")
    executor._record(70006, mid, "KO", 50.0, "k-ref", "placed", place={"_concord_est_qty": 0.6})
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)).isoformat()
    _use(monkeypatch, OrdersClient(orders=[_order("KO", "buy", "filled", "0.7", created=old)]))

    res = reconcile.reconcile_fills(mid)

    assert res["changed"] == 0
    assert positions.club_shares(mid).get("KO") == pytest.approx(0.6)


def test_reconcile_dry_run_writes_nothing(monkeypatch):
    mid = _member("rec-dry@x.com")
    executor._record(70010, mid, "AAPL", 500.0, "d-ref", "placed",
                     place={"data": {"order": {}}, "_concord_est_qty": 2.5})
    _use(monkeypatch, OrdersClient(orders=[_order("AAPL", "buy", "filled", "2.4")]))

    res = reconcile.reconcile_fills(mid, dry_run=True)

    assert res["dry_run"] is True and len(res["reconciled"]) == 1
    assert positions.club_shares(mid).get("AAPL") == pytest.approx(2.5)   # unchanged on disk


# ─── club P&L sign correctness (finding #7) ──────────────────────────────────

def test_club_pnl_is_sign_aware(monkeypatch):
    """Bought 4, sold 2 → 2 held. Old bug summed quantities (6) with no sign;
    now value reflects the 2 net shares."""
    mid = _member("pnl@x.com")
    executor._record(70005, mid, "AAPL", 800.0, "p1", "placed",
                     place={"data": {"order": {"cumulative_quantity": "4.0"}}})
    executor._record(70005, mid, "AAPL", None, "p2", "placed", side="sell",
                     place={"data": {"order": {"cumulative_quantity": "2.0"}}})
    _use(monkeypatch, OrdersClient(positions=[("AAPL", 2.0, 200.0)], quotes={"AAPL": 210.0}))

    res = alloc.club_pnl(mid)

    assert res["positions"] == 1
    assert res["value"] == pytest.approx(2.0 * 210.0)      # 420, not 6*210
    assert res["invested"] == pytest.approx(2.0 * 200.0)   # 400
    assert res["profit"] == pytest.approx(20.0)


# ─── calendar coverage guard (finding #17) ───────────────────────────────────

def test_calendar_covers_and_lapse():
    from app.calendar_us import calendar_covers
    assert calendar_covers(datetime.date(2027, 6, 1)) is True
    assert calendar_covers(datetime.date(2030, 12, 25)) is True
    assert calendar_covers(datetime.date(2031, 1, 2)) is False


# ─── +inf allocation guard (finding #29) ─────────────────────────────────────

def test_allocation_body_rejects_inf_and_nan():
    import pydantic

    from app.routes.members import AllocationBody
    assert AllocationBody(type="dollar", value=100.0).value == 100.0
    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(pydantic.ValidationError):
            AllocationBody(type="dollar", value=bad)


# ─── human quorum + vote-weight freeze (findings #10, #9) ─────────────────────

def test_human_voter_count_and_vote_freeze():
    human = _member("q-human@x.com")
    bot = _member("q-bot@x.com", is_bot=1)
    s = voting.open_session_for(datetime.date(2027, 3, 2))
    voting.set_member_picks(s["id"], human, ["AAPL"])
    voting.set_member_picks(s["id"], bot, ["MSFT"])

    assert voting.voter_count(s["id"]) == 2
    assert voting.human_voter_count(s["id"]) == 1          # bot excluded from quorum
    # not frozen while the ballot is still open
    assert voting.member_in_closed_unexecuted_session(human) is False

    voting.close_and_select(s["id"])
    # frozen once closed-but-not-executed: can't change stake mid-execution
    assert voting.member_in_closed_unexecuted_session(human) is True
