"""Tests for the audited budget/commitment family (A1/A2/D1 + X3-X5).

A1 — the budget block: the account value is read STRICTLY (never collapsed to
     buying power), the club slice is marked for every member before either
     budget branch, and both an unreadable account value and an unquotable club
     holding fail CLOSED with their own skip_reason. The committed figure is
     clamped to the account's deployable capacity, and a commitment that
     exceeds it is written down only after a second materially-clamped CYCLE.
A2 — the write-back: the commitment rides (or is clamped) under a
     compare-and-swap so a mid-cycle stake edit survives, while alloc_applied
     records the budget that was actually deployed. It also DRAINS
     member_settings.carry_pending (the column reconcile credits when it proves
     an errored buy never landed) into slice_carry and zeroes it, reporting the
     amount as msum["carry_drained"].
D1 — alloc.compound_budget nets a NEGATIVE slice_carry (the crumb repair's
     debit) instead of ignoring it, and still floors the sum at zero.
X3-X5 — a daily cap limits deployment and never rewrites the commitment; the
     clamp counter counts CYCLES, not runs; and a dry run leaves no live-sizing
     side effect (no stamp, no counter).

Run from backend/:  python -m pytest tests/ -q
(uses a throwaway temp DB; the Robinhood client + vault are mocked — no network.)
"""
import contextlib
import datetime
import json
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import pytest  # noqa: E402

from app.calendar_us import prev_trading_day, today_et  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_get, set_armed  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import alloc, executor, voting  # noqa: E402

_MIDS: list[int] = []      # members this module created (teardown scope)
_SID_FLOOR = 0             # highest vote_session id that predates this module


def setup_module(_):
    global _SID_FLOOR
    init_db()
    seed_universe()
    with db() as conn:
        _SID_FLOOR = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM vote_sessions").fetchone()[0]


def teardown_module(_):
    # These test modules share one temp DB (CONCORD_DB_PATH via setdefault) and
    # executor.run() scans ALL members/sessions, so this module's LIVE runs must
    # not leave members, sessions or kv stamps behind for the later ones. Scoped
    # to what this module created — never a blanket wipe of another module's rows.
    with db() as conn:
        for mid in _MIDS:
            conn.execute("DELETE FROM executions WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM votes WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
            conn.execute("DELETE FROM kv WHERE key=?", (f"clamp_pending:{mid}",))
        conn.execute("DELETE FROM vote_sessions WHERE id>?", (_SID_FLOOR,))
        conn.execute("DELETE FROM kv WHERE key LIKE 'cycle_budget:%'")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry/BP-poll paths sleep between attempts — skip that in tests."""
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, alloc_type: str = "dollar", alloc_value: float = 100.0,
            alloc_applied: float | None = None, compound: int = 1,
            daily_cap_usd: float = 0.0, opted_in: int = 1) -> int:
    """A connected, opted-in human. Defaults to the live club's shape: a
    COMPOUNDING dollar stake with no daily cap (0 = uncapped)."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,alloc_applied,compound,slice_carry,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (mid, opted_in, 0.0, daily_cap_usd, 10, alloc_type, alloc_value,
             alloc_applied, compound, 0.0, int(time.time())),
        )
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,?,?,?,?,?)",
            (mid, f"ACC{mid}", 1, b"enc-tokens", int(time.time()), int(time.time()), "connected"),
        )
    _MIDS.append(mid)
    return mid


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    s = voting.open_session_for(trade_date)
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


def _hold(mid: int, symbol: str, qty: float, usd: float, sid: int = 97000) -> None:
    """Seed a PRIOR cycle's club holding: a placed buy carrying its estimated
    share count (what club_shares reconstructs the position from)."""
    executor._record(sid, mid, symbol, usd, f"seed:{mid}:{symbol}:{sid}", "placed",
                     place={"_concord_est_qty": qty})
    _backdate(sid, mid)


def _backdate(sid: int, mid: int, days: int = 7) -> None:
    """Prior-cycle rows must look like prior cycles: _record stamps
    created_at=now, and the sell leg's PDT guard would otherwise read a seeded
    holding as bought this morning and refuse to sell it. A full week clears the
    cutoff (the previous trading day's 16:00 ET) whatever weekday — and whatever
    time of day — the suite runs at."""
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND member_id=?",
                     (int(time.time()) - days * 86400, sid, mid))


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _settings_row(mid: int) -> dict:
    with db() as conn:
        return dict(conn.execute(
            "SELECT alloc_type, alloc_value, alloc_applied, COALESCE(slice_carry,0) AS slice_carry, "
            "COALESCE(carry_pending,0) AS carry_pending "
            "FROM member_settings WHERE member_id=?", (mid,)).fetchone())


def _stamp(sid: int, mid: int) -> dict | None:
    raw = kv_get(f"cycle_budget:{sid}:{mid}")
    return json.loads(raw) if raw else None


def _clamp_counter(mid: int) -> dict | None:
    raw = kv_get(f"clamp_pending:{mid}")
    return json.loads(raw) if raw else None


def _audits(action: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE action=?", (action,)).fetchall()
    return [json.loads(r["detail_json"]) for r in rows if r["detail_json"]]


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

    def portfolio_payload(self) -> dict:
        return {"buying_power": {"buying_power": str(self.buying_power)},
                "total_value": str(self.total_value)}

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        return {"data": self.portfolio_payload()}

    def get_positions(self, acct):
        self.calls.append(("get_positions", acct))
        return {"data": {"positions": [
            {"symbol": s, "type": "long", "quantity": str(q)} for s, q in self.positions_list
        ]}}

    def get_quotes(self, symbols):
        self.calls.append(("get_quotes", tuple(symbols)))
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes_map[s])}}
            for s in symbols if s in self.quotes_map
        ]}}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        self.calls.append(("get_equity_orders", acct, placed_agent))
        if self.orders_exc is not None:
            raise self.orders_exc
        orders = self.orders_list
        if placed_agent:   # real API filters server-side; untagged test orders == agentic
            orders = [o for o in orders if o.get("placed_agent", "agentic") == placed_agent]
        return {"data": {"orders": orders}}

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
        """This account's recorded trade calls (the shared client sees every
        member the executor touches — assertions must scope to one account)."""
        return [c for c in self.calls
                if c[0] == kind and c[1] == acct and (symbol is None or c[2] == symbol)]


class RenamedFieldClient(FakeClient):
    """A portfolio payload whose equity field is renamed (or dropped): the
    shape an upstream MCP change ships, and the one the pre-batch
    `total_value or equity_value or buying_power` chain collapsed to cash."""

    def portfolio_payload(self) -> dict:
        return {"buying_power": {"buying_power": str(self.buying_power)},
                "portfolio_value": str(self.total_value)}


def _use_client(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda mid: client)


def _only(monkeypatch, *mids: int) -> None:
    """Scope the pass to this test's members. The suite shares one DB and a LIVE
    run walks every opted-in member in it — without this it would place orders
    in another module's fixtures and rewrite their allocations."""
    real = executor._opted_members
    monkeypatch.setattr(executor, "_opted_members",
                        lambda: [m for m in real() if m["member_id"] in mids])


@contextlib.contextmanager
def _armed():
    """EXECUTION_MODE=live + armed, with the basket-breadth floors waived for a
    single-name test basket (conftest restores settings either way). Only a run
    that WILL PLACE writes a budget stamp, a clamp counter or a write-back, so
    the whole commitment family can only be exercised armed."""
    prev = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 1)
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        yield
    finally:
        object.__setattr__(settings, "execution_mode", prev)
        set_armed(False)


# ─── A1: strict account value, fail-closed ───────────────────────────────────

def test_renamed_total_value_never_collapses_to_buying_power(monkeypatch):
    """A portfolio payload without `total_value`/`equity_value` must NOT fall
    back to buying power: that read a $1,000 account with $12 of idle cash as a
    $12 account, sized a near-zero budget and liquidated the member's slice.
    The member is skipped 'transient_rh_error' BEFORE anything is sold and
    before any budget stamp exists."""
    mid = _member("budget-renamed@x.com", alloc_value=500.0, alloc_applied=500.0)
    _hold(mid, "TSLA", 2.0, 500.0)
    closed = _closed_session(datetime.date(2026, 10, 5), mid, ["AAPL"])   # TSLA dropped
    client = RenamedFieldClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                                positions=[("TSLA", "2.0")],
                                buying_power=12.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "transient_rh_error"
    assert "account value" in row["note"]
    # nothing sold — the dropped name stays put until the account reads cleanly
    assert not client.trades_for("review_sell", f"ACC{mid}")
    assert not client.trades_for("place_sell", f"ACC{mid}")
    assert not client.trades_for("place_buy", f"ACC{mid}")
    # and the skip happened BEFORE the stamp: a bad read must not fix a bad size
    # into the cycle (the stamp is write-once and would be reused all day).
    assert _stamp(closed["id"], mid) is None
    after = _settings_row(mid)
    assert after["alloc_value"] == 500.0 and after["slice_carry"] == 0.0, \
        "a skipped member reaches no write-back at all"


def test_account_value_below_marked_club_value_fails_closed(monkeypatch):
    """An account cannot be worth materially less than the club positions inside
    it, so a total_value under the marked club value is an inconsistent read —
    same fail-closed skip, nothing sold, no stamp."""
    mid = _member("budget-inconsistent@x.com", alloc_value=500.0, alloc_applied=500.0)
    _hold(mid, "TSLA", 2.0, 500.0)
    closed = _closed_session(datetime.date(2026, 10, 6), mid, ["AAPL"])
    # club slice marks at 2 x $250 = $500; the account claims $100.
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "2.0")],
                        buying_power=50.0, total_value=100.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "transient_rh_error"
    assert not client.trades_for("place_sell", f"ACC{mid}")
    assert _stamp(closed["id"], mid) is None


def test_unpriced_club_holding_skips_as_unquotable_not_transient(monkeypatch):
    """A club-held name with no quote undervalues the slice — every kept name
    would read as over target and get trimmed — so the member is skipped. It
    gets its OWN reason: 'unquotable_holding' is a symbol problem an operator
    must look at (watchdog escalates it), not a retry-next-run broker blip."""
    mid = _member("budget-unquotable@x.com", alloc_value=500.0, alloc_applied=500.0)
    _hold(mid, "TSLA", 2.0, 500.0)
    closed = _closed_session(datetime.date(2026, 10, 7), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0},            # no TSLA quote
                        positions=[("TSLA", "2.0")],
                        buying_power=200.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "unquotable_holding"
    assert "TSLA" in row["note"]
    assert not client.trades_for("place_sell", f"ACC{mid}")
    assert _stamp(closed["id"], mid) is None


def test_the_same_fixture_really_trades_when_the_account_reads_cleanly(monkeypatch):
    """Control for the three fail-closed tests above: identical member, holding
    and basket, only the portfolio payload is well-formed. TSLA IS sold, the
    budget IS stamped and the write-back DOES run — so 'nothing was sold' and
    'no stamp exists' there are the guards doing their job, not a fixture whose
    sell leg had been silently disabled (e.g. by the PDT guard)."""
    mid = _member("budget-control@x.com", alloc_value=500.0, alloc_applied=500.0)
    _hold(mid, "TSLA", 2.0, 500.0)
    closed = _closed_session(datetime.date(2026, 10, 20), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "2.0")],
                        buying_power=12.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    assert "skip_reason" not in _mrow(summary, mid)
    assert client.trades_for("place_sell", f"ACC{mid}", "TSLA")
    assert _stamp(closed["id"], mid)["budget"] == 500.0
    # $500 of budget against $12 of settled cash: the rest carries to next cycle
    assert _settings_row(mid)["slice_carry"] > 0.0


# ─── A1/A2: over-commitment auto-clamp ───────────────────────────────────────

def test_over_commitment_clamps_deployment_then_writes_down_on_second_cycle(monkeypatch):
    """The owner's named case: a member whose committed stake exceeds what their
    account can actually deploy (marked club slice + spendable cash) carried the
    excess forever — the budget deferred it every cycle and slice_carry kept
    re-presenting dollars that were never there.

    Contract: deployment is clamped to CAPACITY immediately, but the COMMITMENT
    is only written down after a second materially-clamped CYCLE (one low
    reading — a transient BP dip — must never shrink a stake), and re-running
    the same session does not advance that counter.
    """
    mid = _member("budget-overcommit@x.com", alloc_value=984.0, alloc_applied=700.0)
    _hold(mid, "AAPL", 7.0, 700.0)
    s1 = _closed_session(datetime.date(2026, 10, 8), mid, ["AAPL"])
    # slice marks at $700, only $10 of cash → capacity $710 against a $984
    # commitment; the account is big enough overall, the money just isn't free.
    c1 = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "7.0")],
                    buying_power=10.0, total_value=1500.0)
    _use_client(monkeypatch, c1)
    _only(monkeypatch, mid)

    # A disarmed preview shows the clamp but persists nothing.
    preview = executor.run(s1, force_dry_run=True, phase="both")
    assert _mrow(preview, mid)["budget"] == 710.0
    assert _clamp_counter(mid) is None

    with _armed():
        cycle1 = executor.run(s1, phase="both")
        # …and a re-run of the SAME cycle (split cadence / manual re-fire).
        executor.run(s1, phase="both")

    row1 = _mrow(cycle1, mid)
    assert row1["budget"] == 710.0, "deployment clamped to capacity on cycle 1"
    assert "commit_clamped" not in row1, "one clamped reading never rewrites a stake"
    assert _clamp_counter(mid) == {"n": 1, "sid": s1["id"]}, \
        "the counter tracks CYCLES: a same-session re-run must not advance it"
    after1 = _settings_row(mid)
    assert after1["alloc_value"] == 984.0, "commitment untouched on the first clamp"
    assert after1["alloc_applied"] == 710.0, "…but alloc_applied records what deployed"

    # Cycle 2: same account, same shortfall. The $10 top-up cycle 1 placed is now
    # part of the slice (7.1 shares), so the ride is unchanged at $984.
    s2 = _closed_session(datetime.date(2026, 10, 9), mid, ["AAPL"])
    c2 = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "7.1")],
                    buying_power=10.0, total_value=1500.0)
    _use_client(monkeypatch, c2)
    with _armed():
        cycle2 = executor.run(s2, phase="both")

    row2 = _mrow(cycle2, mid)
    assert row2["commit_clamped"] == {"from": 984.0, "to": 720.0}, \
        "the second clamped cycle matures the write-down (and tells the member)"
    assert row2["budget"] == 720.0
    after2 = _settings_row(mid)
    assert after2["alloc_value"] == 720.0, "commitment now equals deployable capacity"
    assert after2["alloc_applied"] == 720.0


def test_split_cadence_runs_count_one_cycle_for_the_clamp(monkeypatch):
    """The clamp counter is keyed on the SESSION, not on the run. A percent
    stake is recomputed from scratch on every pass (its branch writes no budget
    stamp), so the sell-day and buy-day halves of one cadence would otherwise
    tick the counter twice and mature a write-down after a SINGLE clamped
    cycle — exactly the one-bad-reading case the maturation exists to prevent."""
    mid = _member("budget-splitcadence@x.com", alloc_type="percent", alloc_value=80.0)
    closed = _closed_session(datetime.date(2026, 10, 22), mid, ["AAPL"])
    # 80% of a $1,000 account = $800 committed, but only $50 is spendable and
    # nothing is held yet → capacity $50.
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[],
                        buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        sell = executor.run(closed, phase="sell")
        assert _clamp_counter(mid) == {"n": 1, "sid": closed["id"]}
        buy = executor.run(closed, phase="buy")

    assert _clamp_counter(mid) == {"n": 1, "sid": closed["id"]}, \
        "both halves of one cadence are the SAME cycle"
    assert "commit_clamped" not in _mrow(sell, mid)
    assert "commit_clamped" not in _mrow(buy, mid)
    after = _settings_row(mid)
    assert after["alloc_type"] == "dollar" and after["alloc_value"] == 800.0, \
        "the percent stake converts at the uncapped ride, not a premature write-down"
    assert after["alloc_applied"] == 50.0


def test_pinned_member_is_clamped_without_a_commitment_marker(monkeypatch):
    """A pinned (compound=0) member's write-back never runs, so their committed
    figure cannot be rewritten — maturing a clamp for them would announce an
    adjustment to the member that never happens. Their budget is still clamped
    to capacity; there is simply no counter and no marker, however many clamped
    cycles go by."""
    mid = _member("budget-pinned@x.com", alloc_value=984.0, compound=0)
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[],
                        buying_power=10.0, total_value=1500.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        for day in (23, 26):     # two consecutive materially-clamped cycles
            summary = executor.run(
                _closed_session(datetime.date(2026, 10, day), mid, ["AAPL"]), phase="both")
            row = _mrow(summary, mid)
            assert row["budget"] == 10.0, "clamped to deployable capacity"
            assert "commit_clamped" not in row
            assert _clamp_counter(mid) is None

    assert _settings_row(mid)["alloc_value"] == 984.0, "a pinned commitment never moves"


def test_daily_cap_limits_deployment_but_never_the_commitment(monkeypatch):
    """A daily_cap_usd is a throttle on how much moves in one session — it must
    never be mistaken for the member's committed figure. The commitment keeps
    riding the uncapped slice; only alloc_applied (the honored budget) sees the
    cap, so the next cycle deploys the rest instead of ratcheting the stake down
    to the cap every day."""
    mid = _member("budget-dailycap@x.com", alloc_value=380.0, alloc_applied=280.0,
                  daily_cap_usd=300.0)
    _hold(mid, "AAPL", 2.8, 280.0)
    closed = _closed_session(datetime.date(2026, 10, 12), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "2.8")],
                        buying_power=200.0, total_value=1500.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    row = _mrow(summary, mid)
    # ride = $280 slice + the $100 stake increase = $380; capacity ($480) is
    # ample, so nothing is clamped — the cap alone bounds the deployment.
    assert row["budget"] == 300.0
    assert "commit_clamped" not in row
    after = _settings_row(mid)
    assert after["alloc_value"] == 380.0, "the commitment keeps the uncapped ride"
    assert after["alloc_applied"] == 300.0, "only the honored budget carries the cap"
    assert _stamp(closed["id"], mid)["commit"] == 380.0


# ─── A2: write-back CAS ──────────────────────────────────────────────────────

def test_mid_cycle_stake_edit_survives_the_write_back_cas(monkeypatch):
    """A member who did not vote in the closed session may still edit their
    stake mid-cycle — between the sell day's stamp and the buy day's write-back.
    The commitment UPDATE is a compare-and-swap against the stamped stake, so
    that edit is never clobbered by the figure the cycle started with; only
    alloc_applied moves, and the next cycle deploys
    cv + (edited_value − alloc_applied) = the member's edited figure exactly."""
    mid = _member("budget-stakeedit@x.com", alloc_value=100.0, alloc_applied=100.0)
    _hold(mid, "AAPL", 1.0, 100.0)
    closed = _closed_session(datetime.date(2026, 10, 13), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "1.0")],
                        buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        executor.run(closed, phase="sell")          # stamps the cycle at $100
        assert _stamp(closed["id"], mid)["stake"] == 100.0
        with db() as conn:                          # …member raises their stake
            conn.execute("UPDATE member_settings SET alloc_value=250 WHERE member_id=?", (mid,))
        executor.run(closed, phase="buy")           # …and the buy day writes back

    after = _settings_row(mid)
    assert after["alloc_value"] == 250.0, "the member's edit survives the write-back"
    assert after["alloc_applied"] == 100.0, "alloc_applied = the budget actually deployed"


def test_write_back_drains_a_carry_credit_posted_after_the_stamp(monkeypatch):
    """The two carry columns have different owners, and the write-back is where
    they meet.

    slice_carry is this cycle's OWN undeployed dollars: the write-back WRITES it
    from scratch every buy leg, so a re-run can never accumulate it. Dollars an
    EARLIER cycle failed to deploy arrive asynchronously — reconcile proving at
    the broker that an errored buy never landed — and land in carry_pending,
    which is ADDITIVE and reconcile's alone. A credit can therefore appear at any
    point after this cycle's budget was stamped, which is why the stamped figure
    can never account for it and why the stamp no longer carries a "carry" field
    at all.

    The write-back drains one into the other in a single transaction:
    slice_carry = unplaced_usd + carry_pending, carry_pending = 0 — and reports
    what it drained. Both halves matter: without the add the credit evaporates,
    and without the zeroing the next buy leg would deploy the same dollars again.
    """
    mid = _member("budget-carrycredit@x.com", alloc_value=100.0, alloc_applied=100.0)
    _hold(mid, "AAPL", 1.0, 100.0)
    closed = _closed_session(datetime.date(2026, 10, 21), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "1.0")],
                        buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        executor.run(closed, phase="sell")
        assert "carry" not in _stamp(closed["id"], mid), \
            "the stamp sizes the cycle; it no longer tracks carry for the write-back"
        with db() as conn:      # reconcile proves an errored buy absent, mid-cycle
            conn.execute("UPDATE member_settings SET carry_pending=25 WHERE member_id=?", (mid,))
        summary = executor.run(closed, phase="buy")

    assert _mrow(summary, mid)["carry_drained"] == 25.0, "the run report names the credit"
    after = _settings_row(mid)
    # the buy leg deferred nothing (the name is at target), so the whole $25 is
    # the drained credit — and the pending column is left empty behind it
    assert after["slice_carry"] == 25.0
    assert after["carry_pending"] == 0.0


# ─── X3/X4: dry runs leave no live-sizing side effects ───────────────────────

def test_dry_run_leaves_no_budget_stamp_or_clamp_counter(monkeypatch):
    """A dry run is an inspection: it previews the live sizing (the stamp READ
    stays ungated so a buy-day preview sees the real budget) but writes nothing.
    A stamp would fix a review-only size into the cycle, and a clamp tick would
    let repeated previews mature a write-down of a real commitment."""
    mid = _member("budget-dryrun@x.com", alloc_value=984.0, alloc_applied=700.0)
    _hold(mid, "AAPL", 7.0, 700.0)
    closed = _closed_session(datetime.date(2026, 10, 14), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[("AAPL", "7.0")],
                        buying_power=10.0, total_value=1500.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    summary = executor.run(closed, force_dry_run=True, phase="both")
    executor.run(closed, force_dry_run=True, phase="both")   # …and again

    assert _mrow(summary, mid)["budget"] == 710.0, "the clamp is previewed"
    assert _stamp(closed["id"], mid) is None
    assert _clamp_counter(mid) is None
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert _settings_row(mid)["alloc_applied"] == 700.0


# ─── A1: crash continuation ──────────────────────────────────────────────────

def test_crash_continuation_carries_the_prior_cycle_budget_forward(monkeypatch):
    """A 'both' day that placed its SELLS and then died before any buy leaves
    the proceeds as cash: club positions are gone and the cash has not settled,
    so a fresh positions-only mark sizes ~$0 and the slice silently shrinks by
    the sold notional. When the previous session never executed, sold for this
    member and bought nothing, this run IS that cycle's buy leg — it reuses the
    prior stamp verbatim and retires it so it can never fold forward twice.

    The stamp is budget/stake/commit and nothing else: carry is no longer part of
    it, because the write-back now reconciles the two carry columns against each
    other (drain carry_pending into slice_carry) rather than against a stamped
    figure — so a stamp folded forward from an interrupted cycle carries no carry
    state with it."""
    mid = _member("budget-crash@x.com", alloc_value=400.0, alloc_applied=400.0)
    # Dated relative to TODAY on purpose: the carry-forward only fires while the
    # interrupted session is inside STALE_BASKET_DAYS, so a literal date would
    # quietly stop exercising the branch once the calendar rolled past it.
    today = today_et()
    prev = _closed_session(prev_trading_day(today), mid, ["TSLA"])
    executor._record(prev["id"], mid, "TSLA", None,
                     executor.ref_id_for(prev["id"], mid, "SELL:TSLA"), "placed",
                     place={"_concord_est_qty": 1.6}, side="sell")
    prev_key = f"cycle_budget:{prev['id']}:{mid}"
    with db() as conn:
        conn.execute("INSERT INTO kv(key,value) VALUES(?,?)",
                     (prev_key, json.dumps({"budget": 400.0, "stake": 400.0,
                                            "commit": 400.0})))

    closed = _closed_session(today, mid, ["AAPL"])
    # the interrupted cycle's world: no club positions left, proceeds not yet
    # credited (BP $0) — capacity alone would size this cycle at $0.
    client = FakeClient(quotes={"AAPL": 100.0}, positions=[],
                        buying_power=0.0, total_value=400.0)
    _use_client(monkeypatch, client)
    _only(monkeypatch, mid)

    with _armed():
        summary = executor.run(closed, phase="both")

    row = _mrow(summary, mid)
    assert row["buying_power"] == 0.0, "fresh capacity really was zero this run"
    assert row["budget"] == 400.0, "the interrupted cycle's budget carried forward"
    assert _stamp(closed["id"], mid) == {"budget": 400.0, "stake": 400.0,
                                         "commit": 400.0}
    assert kv_get(prev_key) is None, "the prior stamp is retired — never folds twice"
    assert any(a.get("member_id") == mid and a.get("from_session") == prev["id"]
               for a in _audits("budget_carried_forward"))


# ─── D1: compound_budget nets the crumb-repair debit ─────────────────────────

def test_compound_budget_nets_a_negative_slice_carry():
    """slice_carry goes NEGATIVE when the crumb repair spends $1 of account cash
    on club shares: those shares land in the slice and would otherwise inflate
    the next mark, compounding the member's stake by every repair. Netting the
    debit keeps the committed stake flat — and the sum still floors at zero, so
    a large debit can only bootstrap (never size negative)."""
    # $100 committed, slice marks at $100, three $1 repairs fronted from cash
    assert alloc.compound_budget(100.0, 100.0, 100.0, 1000.0, slice_carry=-3.0) == 97.0
    # (the pre-batch max(0, carry) ignored the debit entirely and rode the full $100)
    assert alloc.compound_budget(100.0, 100.0, 100.0, 1000.0, slice_carry=0.0) == 100.0
    # a debit larger than the slice floors the marked value at zero → below the
    # $1 bootstrap line, so the member redeploys their committed stake, never a
    # negative or runaway figure
    assert alloc.compound_budget(100.0, 100.0, 2.0, 1000.0, slice_carry=-50.0) == 100.0
    assert alloc.compound_budget(100.0, 100.0, 2.0, 40.0, slice_carry=-50.0) == 40.0
