"""The 2026-09-30 fail-open audit, second batch: siblings of the cursor bug.

Every finding here is a figure the executor persisted from an input it never
checked — the class that turned a truncated order feed into a $990→$82
commitment. Each test fails a named guard when that guard is reverted.

  F3  an unreadable buying_power read as $0 (the clamp then matured on it);
  F7  a club SELL that did not place was still spent by the buy leg — from
      the member's own cash — and its value carried on top of the position;
  F8  a pinned cycle never recorded the figure it honored, so a stake edit
      made while compounding was off was applied twice when it came back on;
  F9  crash-continuation adopted a budget whose errored buy reconcile had
      already credited to carry (the two windows overlap on a weekly cadence);
  F10 carry counted on both sides of the capacity clamp, so an unfunded carry
      could never mature out;
  C2  the partial-fill credit read a field the broker never sends.

Run from backend/:  python -m pytest tests/test_audit_batch2.py -q
(throwaway temp DB; the Robinhood client + vault are mocked — no network.)
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

from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_get, set_armed  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.robinhood.feed import executed_notional  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402

_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


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
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
            conn.execute("DELETE FROM kv WHERE key IN (?,?)",
                         (f"clamp_pending:{mid}", f"acct_type:{mid}"))
        for sid in _SESSIONS:
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM run_reports WHERE trade_date IN "
                         "(SELECT trade_date FROM vote_sessions WHERE id=?)", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))
            for pfx in ("cycle_budget", "carry_drained", "repair_spend", "strand_hold"):
                conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"{pfx}:{sid}:%",))


class Broker:
    """Scripted broker double: static positions, per-symbol blocked sells."""

    def __init__(self, *, quotes=None, positions=None, buying_power=100.0,
                 total_value=1000.0, portfolio=None, blocked_sells=(), orders=None):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.buying_power = buying_power
        self.total_value = total_value
        self.portfolio = portfolio
        self.blocked_sells = set(blocked_sells)
        self.orders = orders or []

    def get_portfolio(self, acct):
        if self.portfolio is not None:
            return self.portfolio
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

    def get_positions(self, acct, cursor=None):
        return {"data": {"positions": [{"symbol": s, "type": "long", "quantity": str(q)}
                                       for s, q in self.positions_list]}}

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes_map[s])}}
            for s in symbols if s in self.quotes_map]}}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        return {"data": {"orders": list(self.orders), "next": None}}

    def review_market_buy(self, acct, symbol, amt):
        self.calls.append(("review_buy", acct, symbol, amt))
        return {"data": {"order_checks": {}}}

    def review_market_sell(self, acct, symbol, qty):
        self.calls.append(("review_sell", acct, symbol, qty))
        if symbol in self.blocked_sells:
            return {"data": {"order_checks": {"alertType": "EQUITY_NOT_ENOUGH_SHARES"}}}
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, acct, symbol, amt, ref_id=None):
        self.calls.append(("place_buy", acct, symbol, amt))
        return {"data": {"order": {"id": f"ord-{symbol}", "state": "queued"}}}

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.calls.append(("place_sell", acct, symbol, qty))
        return {"data": {"order": {"id": f"ord-{symbol}-s", "state": "queued"}}}

    def buys(self, acct):
        return [(c[2], float(c[3])) for c in self.calls if c[0] == "place_buy" and c[1] == acct]


def _member(email: str, *, alloc_value: float, alloc_applied: float | None,
            compound: int = 1, slice_carry: float = 0.0) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,max_orders,alloc_type,alloc_value,"
            "alloc_applied,compound,slice_carry,updated_at) VALUES(?,1,25,'dollar',?,?,?,?,?)",
            (mid, alloc_value, alloc_applied, compound, slice_carry, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?, 'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    _MEMBERS.append(mid)
    return mid


def _placed_buy(sid: int, mid: int, sym: str, qty: float, days_ago: float = 3) -> None:
    executor._record(sid, mid, sym, qty * 100.0, executor.ref_id_for(sid, mid, f"seed:{sym}"),
                     "placed", place={"_concord_est_qty": qty})
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol=? "
                     "AND session_id=?", (int(time.time() - days_ago * 86400), mid, sym, sid))


def _closed_session(trade_date: datetime.date, mid: int, picks: list[str]) -> dict:
    object.__setattr__(settings, "top_n", max(settings.top_n, len(picks)))
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], mid, picks)
    return voting.close_and_select(s["id"])


@contextlib.contextmanager
def _armed():
    prev = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 1)
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        yield
    finally:
        set_armed(False)
        object.__setattr__(settings, "execution_mode", prev)


def _run(monkeypatch, mid, closed, client, *, live=False, phase="both"):
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    # the both-day proceeds poll waits up to 120s against a broker whose cash
    # never moves — these tests are about sizing, not settlement
    monkeypatch.setattr(executor, "BP_POLL_MAX_S", 0)
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)
    if live:
        with _armed():
            summary = executor.run(closed, force_dry_run=False, phase=phase)
    else:
        summary = executor.run(closed, force_dry_run=True, phase=phase)
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _settings(mid):
    with db() as conn:
        return dict(conn.execute(
            "SELECT alloc_value, alloc_applied, COALESCE(slice_carry,0) AS slice_carry, "
            "COALESCE(carry_pending,0) AS carry_pending FROM member_settings WHERE member_id=?",
            (mid,)).fetchone())


# ─── F3: buying power fails closed ───────────────────────────────────────────

def test_unreadable_buying_power_skips_the_member(monkeypatch):
    """A renamed inner key used to read as $0 of buying power — no skip, no
    audit — which deferred every buy and, after two such cycles, matured the
    clamp and wrote the stake down. Now it is a skip, like total_value."""
    mid = _member("bp-unreadable@x.com", alloc_value=200.0, alloc_applied=150.0)
    _placed_buy(90910, mid, "AAPL", 1.5)
    closed = _closed_session(datetime.date(2033, 1, 4), mid, ["AAPL"])
    client = Broker(quotes={"AAPL": 100.0}, positions=[("AAPL", "1.5")],
                    portfolio={"data": {"buying_power": {"amount": "50.0"},
                                        "total_value": "500.0"}})
    row = _run(monkeypatch, mid, closed, client, live=True)
    assert row["skip_reason"] == "transient_rh_error" and "buying power" in row["note"]
    assert kv_get(f"cycle_budget:{closed['id']}:{mid}") is None, "no stamp on a bad read"
    assert _settings(mid)["alloc_value"] == 200.0


# ─── F7: an unsold club name is not proceeds ─────────────────────────────────

def test_a_blocked_exit_is_not_spent_by_the_buy_leg(monkeypatch):
    """Club holds X ($10, dropped from the basket) and A ($20, kept); the
    member has $10 of their OWN cash idle. X's sell is blocked. The budget
    ($30) was sized with X in it, so the buy leg must not fund X's share of
    the new basket from the idle cash — and must not carry X's value either,
    since X is still in the slice and the next mark counts it again."""
    mid = _member("unsold-exit@x.com", alloc_value=30.0, alloc_applied=30.0)
    _placed_buy(90911, mid, "TSLA", 0.1)              # X: dropped from the basket
    _placed_buy(90911, mid, "AAPL", 0.2)              # A: kept, $5 over its target
    closed = _closed_session(datetime.date(2033, 1, 5), mid, ["AAPL", "MSFT"])
    client = Broker(quotes={"TSLA": 100.0, "AAPL": 100.0, "MSFT": 100.0},
                    positions=[("TSLA", "0.1"), ("AAPL", "0.2")],
                    buying_power=10.0, total_value=40.0, blocked_sells={"TSLA"})

    row = _run(monkeypatch, mid, closed, client, live=True)

    assert row.get("unsold_club_usd") == pytest.approx(10.0)
    assert client.buys(f"ACC{mid}") == [], "TSLA's value is still TSLA — nothing to redeploy"
    # the shared test DB hands this same fake to every member the executor
    # touches — scope to this account, as every assertion here must
    sells = [(c[2], c[3]) for c in client.calls if c[0] == "place_sell" and c[1] == f"ACC{mid}"]
    assert [x[0] for x in sells] == ["AAPL"], f"only the trim goes out: {sells}"
    s = _settings(mid)
    # AAPL's trim ($5 over its $15 target) is real proceeds and carries; TSLA's $10 does not
    assert s["slice_carry"] == pytest.approx(5.0, abs=0.02), s
    assert s["alloc_value"] == pytest.approx(30.0), "the commitment is not rewritten upward"


# ─── F8: a pinned cycle records what it honored ──────────────────────────────

def test_pinned_cycle_records_alloc_applied(monkeypatch):
    """Compounding at $100, then off; the member raises the stake to $200 and a
    pinned cycle deploys it. When compounding comes back on the ride must be
    cv + (200 − 200), not cv + (200 − 100): the raise was already honored."""
    mid = _member("pinned-applied@x.com", alloc_value=100.0, alloc_applied=100.0)
    _placed_buy(90912, mid, "AAPL", 1.0)
    with db() as conn:
        conn.execute("UPDATE member_settings SET compound=0, alloc_value=200.0 WHERE member_id=?",
                     (mid,))
    closed = _closed_session(datetime.date(2033, 1, 6), mid, ["AAPL"])
    client = Broker(quotes={"AAPL": 100.0}, positions=[("AAPL", "1.0")],
                    buying_power=500.0, total_value=1000.0)
    row = _run(monkeypatch, mid, closed, client, live=True)
    assert row["budget"] == pytest.approx(200.0)
    assert _settings(mid)["alloc_applied"] == pytest.approx(200.0), \
        "the pinned cycle honored $200 and must say so"

    with db() as conn:
        conn.execute("UPDATE member_settings SET compound=1 WHERE member_id=?", (mid,))
    client.positions_list = [("AAPL", "2.0")]          # the pinned cycle's buy landed
    later = _closed_session(datetime.date(2033, 1, 7), mid, ["AAPL"])
    row2 = _run(monkeypatch, mid, later, client, live=True)
    assert row2["budget"] == pytest.approx(200.0), "not 200 + (200 − 100)"
    assert client.buys(f"ACC{mid}") == [("AAPL", 100.0)], "nothing bought twice"


# ─── F10: carry is cash or it is nothing ─────────────────────────────────────

def test_a_carry_the_account_cannot_fund_matures_the_clamp(monkeypatch):
    """$100 committed, $93 in positions, $7 of carry — and $0 of buying power:
    the carry's dollars are gone (spent by hand). Counting carry inside
    capacity made ride ≤ capacity an identity, so this never clamped; now the
    second cycle writes the commitment down to what the account can fund."""
    mid = _member("carry-unfunded@x.com", alloc_value=100.0, alloc_applied=100.0, slice_carry=7.0)
    _placed_buy(90913, mid, "AAPL", 0.93)
    client = Broker(quotes={"AAPL": 100.0}, positions=[("AAPL", "0.93")],
                    buying_power=0.0, total_value=100.0)
    first = _closed_session(datetime.date(2033, 1, 11), mid, ["AAPL"])
    row1 = _run(monkeypatch, mid, first, client, live=True)
    assert "commit_clamped" not in row1, "one cycle never shrinks a stake"
    with db() as conn:                                  # the carry stays on the books meanwhile
        conn.execute("UPDATE member_settings SET slice_carry=7.0 WHERE member_id=?", (mid,))
    second = _closed_session(datetime.date(2033, 1, 12), mid, ["AAPL"])
    row2 = _run(monkeypatch, mid, second, client, live=True)
    assert row2.get("commit_clamped") == {"from": pytest.approx(100.0), "to": pytest.approx(93.0)}, row2
    assert _settings(mid)["alloc_value"] == pytest.approx(93.0)


# ─── F9: adoption reverses a credit reconcile already granted ────────────────

def test_adopted_budget_takes_back_a_credit_reconcile_already_granted(monkeypatch):
    """Weekly cadence: the interrupted cycle is 7 days old, inside the 10-day
    adoption window but past reconcile's 5-day age-out. Reconcile has already
    credited the errored buy's $60 to carry_pending and flipped the row to
    'skipped'. Adopting the stamped $100 budget re-deploys those dollars, so
    the credit must be taken back — or the write-back drains it into carry and
    the next cycle spends the same $60 again."""
    prev_cadence = settings.cycle_cadence
    object.__setattr__(settings, "cycle_cadence", "weekly")
    monkeypatch.setattr(executor, "_cycle_gap_days", lambda: 10)   # weekly, whatever the env
    mid = _member("adopt-credit@x.com", alloc_value=100.0, alloc_applied=100.0)
    week_ago = datetime.date.today() - datetime.timedelta(days=7)
    prev = _closed_session(week_ago, mid, ["AAPL"])
    # the interrupted 'both' day: sold, then the buy errored and the run died
    executor._record(prev["id"], mid, "OLD", None, executor.ref_id_for(prev["id"], mid, "SELL:OLD"),
                     "placed", place={"_concord_est_qty": 1.0}, side="sell")
    executor._record(prev["id"], mid, "AAPL", 60.0, executor.ref_id_for(prev["id"], mid, "AAPL"),
                     "skipped", error="aged out — credited",
                     place={"_concord_carry_credited": 60.0})
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND member_id=?",
                     (int(time.time() - 7 * 86400), prev["id"], mid))
        conn.execute("UPDATE member_settings SET carry_pending=60.0 WHERE member_id=?", (mid,))
        conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (f"cycle_budget:{prev['id']}:{mid}",
                      json.dumps({"budget": 100.0, "stake": 100.0, "commit": 100.0})))
    today = _closed_session(datetime.date.today(), mid, ["AAPL"])
    client = Broker(quotes={"AAPL": 100.0}, positions=[], buying_power=100.0, total_value=100.0)

    row = _run(monkeypatch, mid, today, client, live=True)

    assert row["budget"] == pytest.approx(100.0), "the interrupted cycle's budget is adopted"
    s = _settings(mid)
    assert s["carry_pending"] == 0.0 and s["slice_carry"] == pytest.approx(0.0), \
        "the $60 credit was reversed, not drained on top of the adopted budget"
    with db() as conn:
        r = conn.execute("SELECT status, place_json FROM executions WHERE session_id=? AND member_id=? "
                         "AND symbol='AAPL'", (prev["id"], mid)).fetchone()
    assert r["status"] == "superseded"
    assert json.loads(r["place_json"]).get("_concord_credit_reversed") == 60.0
    assert kv_get(f"cycle_budget:{prev['id']}:{mid}") is None, "old stamp retired"
    object.__setattr__(settings, "cycle_cadence", prev_cadence)


# ─── C2: the filled dollars come from the executions ─────────────────────────

def test_executed_notional_is_read_from_the_fills_the_broker_sends():
    real = {"cumulative_quantity": "0.005685", "average_price": "207.543100",
            "executions": [{"price": "207.543100", "quantity": "0.005685"}]}
    assert executed_notional(real) == pytest.approx(0.005685 * 207.5431)
    assert executed_notional({"cumulative_quantity": "2", "average_price": "10"}) == pytest.approx(20.0)
    assert executed_notional({"executed_notional": "5.00"}) is None, "a field the broker never sends"
    assert executed_notional({}) is None
