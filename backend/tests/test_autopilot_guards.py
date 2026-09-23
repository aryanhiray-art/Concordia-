"""Autopilot hardening guards — G1 (missed execute window) + G2 (thin basket).

G1 — concord-execute.timer is Persistent=true, so a reboot spanning Mon 10:15
     re-fires the job on boot. Inside the trading morning that late fire simply
     runs; PAST settings.execute_catchup_until (15:45 ET) a SCHEDULED run must
     stand down (no orders, 'missed_execute' audit) instead of trading the
     morning's consensus into the close. Manual triggers bypass — the human
     pressing the button is the judgment.

G2 — a near-empty ballot (one human voting one stock) must never become a live
     1-2 name "basket": below settings.min_basket_size picks the run downgrades
     to review-only (dry-runs still simulate), and adminview.will_place_state
     mirrors the reason so the console explains it before 10:15.

Run from backend/:  python -m pytest tests/test_autopilot_guards.py -q
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

import app.calendar_us as cal  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, set_armed  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import adminview, executor, voting  # noqa: E402


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    # House pattern (see test_audit_fixes): these modules share one temp DB and
    # executor.run() in later modules scans ALL members/executions — reset the
    # mutable tables so this module's members/sessions don't leak into the
    # other modules' summaries (test_consensus indexes summary["members"][0]).
    with db() as conn:
        for t in ("executions", "votes", "bot_picks", "vote_sessions",
                  "voting_power_history", "login_events", "api_keys", "audit_log",
                  "rh_accounts", "member_settings", "members", "run_reports"):
            conn.execute(f"DELETE FROM {t}")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


def _pin_now(monkeypatch, hh: int, mm: int) -> None:
    """Pin calendar_us.now_et to today's date at hh:mm ET."""
    d = cal.today_et()
    fake = datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=cal.ET)
    monkeypatch.setattr(cal, "now_et", lambda: fake)


def _member(email: str, alloc_value: float = 50.0) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,max_orders,alloc_type,"
            "alloc_value,updated_at) VALUES(?,1,10,'percent',?,?)",
            (mid, alloc_value, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?,'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    return mid


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    s = voting.open_session_for(trade_date)
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


class FakeClient:
    """Minimal scripted Robinhood double (records every call)."""

    def __init__(self, quotes=None, buying_power=1000.0, total_value=1000.0):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.buying_power = buying_power
        self.total_value = total_value

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

    def get_positions(self, acct):
        self.calls.append(("get_positions", acct))
        return {"data": {"positions": []}}

    def get_quotes(self, symbols):
        self.calls.append(("get_quotes", tuple(symbols)))
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes_map[s])}}
            for s in symbols if s in self.quotes_map]}}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        self.calls.append(("get_equity_orders", acct))
        return {"data": {"orders": []}}

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


# ─── G1: missed execute window ───────────────────────────────────────────────

def test_schedule_trigger_past_cutoff_stands_down(monkeypatch):
    """A scheduled fire past the catch-up cutoff must never reach run() — it
    returns an idle summary, audits 'missed_execute', and leaves a report row."""
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    _pin_now(monkeypatch, 16, 0)

    def _boom(*_a, **_k):
        raise AssertionError("run() must not be reached past the catch-up cutoff")
    monkeypatch.setattr(executor, "run", _boom)

    out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
    assert out.get("missed_window") is True
    assert out["orders"] == []
    assert "missed execute window" in out["note"]
    today = cal.today_et().isoformat()
    assert out["trade_date"] == today
    with db() as conn:
        row = conn.execute(
            "SELECT target, detail_json FROM audit_log WHERE action='missed_execute' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        rpt = conn.execute(
            "SELECT summary_json FROM run_reports ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and row["target"] == today
    assert json.loads(row["detail_json"])["cutoff"] == "15:45"
    assert json.loads(rpt["summary_json"]).get("missed_window") is True


def test_manual_trigger_bypasses_cutoff(monkeypatch):
    """The human pressing Execute-now IS the judgment — no window check."""
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    _pin_now(monkeypatch, 16, 0)
    fake_sess = {"id": 424242, "status": "closed",
                 "trade_date": cal.today_et().isoformat()}
    monkeypatch.setattr(executor.voting, "session_for_trade_date", lambda td: fake_sess)
    called = {}

    def _fake_run(session, **kw):
        called["session"] = session
        return {"ok": True, "note": "ran", "orders": []}
    monkeypatch.setattr(executor, "run", _fake_run)

    out = executor._run_today_locked(force_dry_run=True, trigger="manual:owner@x.test")
    assert called["session"] is fake_sess
    assert out["note"] == "ran"


def test_schedule_inside_window_still_runs(monkeypatch):
    """A late-but-same-morning Persistent fire (e.g. 10:40 after a reboot)
    simply runs — the window guard only bites past the cutoff."""
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    _pin_now(monkeypatch, 10, 40)
    fake_sess = {"id": 424243, "status": "closed",
                 "trade_date": cal.today_et().isoformat()}
    monkeypatch.setattr(executor.voting, "session_for_trade_date", lambda td: fake_sess)
    called = {}

    def _fake_run(session, **kw):
        called["session"] = session
        return {"ok": True, "note": "ran", "orders": []}
    monkeypatch.setattr(executor, "run", _fake_run)

    out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
    assert called["session"] is fake_sess
    assert out["note"] == "ran"


def test_garbage_cutoff_knob_falls_back_to_default(monkeypatch):
    """An unparseable EXECUTE_CATCHUP_UNTIL must not break the guard — it
    falls back to 15:45 rather than raising or trading."""
    object.__setattr__(settings, "execute_catchup_until", "not-a-time")
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    _pin_now(monkeypatch, 16, 0)
    monkeypatch.setattr(executor, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must stand down")))
    out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
    assert out.get("missed_window") is True


def test_missed_execute_audits_once_across_a_retry_storm(monkeypatch):
    """Persistent replays / repeated unit starts past the cutoff must leave ONE
    missed_execute audit row for the day — while every fire still writes its
    own run_reports row (that is the proof each fire happened)."""
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    _pin_now(monkeypatch, 16, 0)
    monkeypatch.setattr(executor, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must stand down")))
    today = cal.today_et().isoformat()
    with db() as conn:
        conn.execute("DELETE FROM audit_log WHERE action='missed_execute'")
        n_reports_before = conn.execute("SELECT COUNT(*) FROM run_reports").fetchone()[0]
    for _ in range(3):
        out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
        assert out.get("missed_window") is True
    with db() as conn:
        n_audit = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='missed_execute' AND target=?",
            (today,)).fetchone()[0]
        n_reports = conn.execute("SELECT COUNT(*) FROM run_reports").fetchone()[0]
    assert n_audit == 1, "audited once per missed day, not once per retry"
    assert n_reports == n_reports_before + 3, "every fire still leaves a report row"


def test_misconfigured_cutoff_cannot_block_the_on_time_run(monkeypatch):
    """EXECUTE_CATCHUP_UNTIL at/before execute_at would otherwise stand down
    the NORMAL scheduled fire every cycle day — silently skipping the week's
    trade. The guard must give the on-time fire a 30-minute grace instead."""
    # Derive both instants from execute_at rather than hardcoding a clock: this
    # test pinned 09:31 and silently stopped testing the grace boundary the day
    # the scheduled time moved to 10:15.
    _eh, _em = (int(x) for x in settings.execute_at.split(":"))
    _exec_min = _eh * 60 + _em
    object.__setattr__(settings, "execute_catchup_until",
                       f"{(_exec_min - 31) // 60:02d}:{(_exec_min - 31) % 60:02d}")
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "both")
    fake_sess = {"id": 424244, "status": "closed",
                 "trade_date": cal.today_et().isoformat()}
    monkeypatch.setattr(executor.voting, "session_for_trade_date", lambda td: fake_sess)
    called = {}
    monkeypatch.setattr(executor, "run",
                        lambda session, **kw: called.update(session=session)
                        or {"ok": True, "note": "ran", "orders": []})
    _on_time = _exec_min + 1                   # the on-time fire, one minute late
    _pin_now(monkeypatch, _on_time // 60, _on_time % 60)
    out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
    assert out["note"] == "ran" and called["session"] is fake_sess
    # ...and once the grace lapses the stand-down still protects the close
    _past = _exec_min + 31                     # > execute_at + the 30 min grace
    _pin_now(monkeypatch, _past // 60, _past % 60)
    monkeypatch.setattr(executor, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must stand down past the grace")))
    out = executor._run_today_locked(force_dry_run=True, trigger="schedule")
    assert out.get("missed_window") is True


# ─── G2: thin-basket floor ───────────────────────────────────────────────────

def test_thin_closed_basket_live_downgrades_to_review_only(monkeypatch):
    """LIVE + ARMED with a 2-pick basket (< floor 5): no order may place; the
    run reviews everything and says why."""
    mid = _member("thin-live@ag.test")
    closed = _closed_session(datetime.date(2028, 3, 6), mid, ["AAPL", "MSFT"])
    client = FakeClient(quotes={"AAPL": 100.0, "MSFT": 200.0})
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    object.__setattr__(settings, "execution_mode", "live")
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False)
    finally:
        set_armed(False)
    assert summary["will_place"] is False
    assert summary.get("thin_basket") is True
    assert "basket too thin (2 < 5)" in summary["note"]
    assert not [c for c in client.calls if c[0].startswith("place_")], \
        "a thin basket must never place a real order"
    buys = [o for o in summary["orders"] if o["side"] == "buy"]
    assert buys and all(o["status"] == "reviewed" for o in buys), \
        "the run still simulates (review-only), it does not vanish"


def test_thin_basket_dry_run_still_simulates(monkeypatch):
    """Dry-run mode is unchanged by the floor: orders review as before and the
    summary carries no thin_basket downgrade (there was nothing to downgrade)."""
    mid = _member("thin-dry@ag.test")
    closed = _closed_session(datetime.date(2028, 3, 7), mid, ["AAPL", "MSFT"])
    client = FakeClient(quotes={"AAPL": 100.0, "MSFT": 200.0})
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    summary = executor.run(closed, force_dry_run=True)
    assert summary.get("thin_basket") is None
    buys = [o for o in summary["orders"] if o["side"] == "buy"]
    assert buys and all(o["status"] == "reviewed" for o in buys)


def test_basket_at_exactly_the_floor_places(monkeypatch):
    """Boundary: N == MIN_BASKET_SIZE is NOT thin — the gate is strictly
    below-the-floor, so a 2-pick basket with floor 2 places live orders."""
    mid = _member("thin-boundary@ag.test")
    closed = _closed_session(datetime.date(2028, 3, 10), mid, ["AAPL", "MSFT"])
    client = FakeClient(quotes={"AAPL": 100.0, "MSFT": 200.0})
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 2)   # conftest restores
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False)
    finally:
        set_armed(False)
    assert summary["will_place"] is True
    assert summary.get("thin_basket") is None
    assert [c for c in client.calls if c[0] == "place_buy"], \
        "N == floor passes the gate — orders place"


def test_adminview_mirrors_the_thin_basket_reason(monkeypatch):
    """will_place_state must explain the same gate the executor enforces, with
    the same wording — and only for a CLOSED (decided) basket."""
    mid = _member("thin-admin@ag.test")
    closed = _closed_session(datetime.date(2028, 3, 8), mid, ["AAPL", "MSFT"])
    monkeypatch.setattr(voting, "latest_session",
                        lambda status: closed if status == "closed" else None)
    st = adminview.will_place_state()
    assert "basket too thin (2 < 5)" in st["reasons"]
    # a floor the basket satisfies produces NO reason
    object.__setattr__(settings, "min_basket_size", 2)
    st = adminview.will_place_state()
    assert not any("basket too thin" in r for r in st["reasons"])


def test_adminview_never_judges_an_open_ballot(monkeypatch):
    """An open session has no decided basket — the thin reason must not appear
    for it (the executor judges the basket at close)."""
    open_sess = {"id": 909090, "status": "open", "trade_date": "2028-03-09",
                 "closes_at": int(time.time()) + 3600, "picks_json": None}
    monkeypatch.setattr(voting, "latest_session",
                        lambda status: open_sess if status == "open" else None)
    monkeypatch.setattr(voting, "human_voter_count", lambda sid: 1)
    st = adminview.will_place_state()
    assert not any("basket too thin" in r for r in st["reasons"])
