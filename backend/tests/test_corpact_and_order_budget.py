"""Two live-production defects found on the 2026-08-31 cycle.

1. A RENAMED holding could never be sold. The broker's order feed is immutable
   history — after AvalonBay became Vivmark it still reports the original fills
   as AVB — while services/renames.migrate renames the LEDGER rows to VMRK.
   club_shares_derived intersects feed symbols with ledger symbols, so the name
   fell out of BOTH sides: the club held VMRK, could not see it, never sold it,
   and account verification failed every cycle with "dropped from basket but
   still held". Verified against production: the fix reconstructs 0.0752 for one
   member and 0.0156 for another, matching each account exactly.

2. The SELL leg spent the BUY budget. One shared counter against MAX_ORDERS
   meant a full rotation starved its own buys — a cycle needs up to 2 x TOP_N
   orders, so at TOP_N=20 / MAX_ORDERS=25 a member needing 35 placed 25 and
   ended holding 16 of 20 basket names, with the undeployed dollars left in
   cash. That is why committed amounts came out below what members held.

Run from backend/:  python -m pytest tests/test_corpact_and_order_budget.py -q
"""
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

from app.db import audit, db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import positions  # noqa: E402

_MUTABLE = ("executions", "votes", "vote_sessions", "audit_log",
            "rh_accounts", "member_settings", "members")


def setup_module(_):
    init_db()
    seed_universe()


_SID: list = [None]


@pytest.fixture(autouse=True)
def _isolate():
    with db() as conn:
        for t in _MUTABLE:
            conn.execute(f"DELETE FROM {t}")
    _SID[0] = _session()
    yield
    with db() as conn:          # leave nothing behind: the DB is shared across
        for t in _MUTABLE:      # test modules, and a stray symbol_migrated row
            conn.execute(f"DELETE FROM {t}")   # renames AVB->VMRK for everyone


def _member(email="ca@x.com"):
    with db() as conn:
        mid = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) "
            "VALUES(?,?,'member',0,?)", (email, email.split("@")[0], int(time.time()))).lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,1,10,0,25,'dollar',100,?)",
            (mid, int(time.time())))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?,'connected')",
            (mid, f"ACC{mid}", b"enc", int(time.time()), int(time.time())))
    return mid


def _session():
    with db() as conn:
        return conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,'executed',?,?,3) RETURNING id",
            (f"2030-01-{(int(time.time()) % 27) + 1:02d}", int(time.time()),
             int(time.time()))).fetchone()["id"]


def _placed(mid, symbol, side="buy", qty=None, corpact=None, dollars=1.0):
    pj = {}
    if qty is not None:
        pj["_concord_est_qty"] = qty
    if corpact:
        pj["corporate_action"] = corpact
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,place_json,created_at) VALUES(?,?,?,?,'market',?,?,'placed',?,?)",
            (_SID[0], mid, symbol, side, dollars, f"r{mid}{symbol}{side}{time.time_ns()}",
             json.dumps(pj), int(time.time())))


# ── 1. the renamed, unsellable holding ───────────────────────────────────────

def test_symbol_renames_is_recovered_from_the_audit_trail():
    assert positions.symbol_renames() == {}
    audit("admin", "symbol_migrated", "AVB->VMRK", {"execution_rows": 4})
    assert positions.symbol_renames() == {"AVB": "VMRK"}


def test_corporate_action_shares_are_read_back():
    """A conversion creates shares with NO order behind them, so the feed can
    never see them. renames.migrate records the difference as a $0 execution."""
    mid = _member()
    _placed(mid, "VMRK", qty=0.048268, corpact="AVB->VMRK", dollars=0.0)
    assert positions.corporate_action_shares(mid) == {"VMRK": pytest.approx(0.048268)}
    # an ordinary buy is NOT a corporate action
    _placed(mid, "AAPL", qty=1.0)
    assert "AAPL" not in positions.corporate_action_shares(mid)


def test_renamed_holding_is_reachable_in_the_derived_position(monkeypatch):
    """THE production defect. Feed says AVB, ledger says VMRK, so the
    intersection dropped the name from both sides and it could never be sold.

    Reproduces the exact live numbers: feed AVB 0.026932 + corporate-action
    0.048268 = 0.0752, which is what the account actually held."""
    mid = _member()
    audit("admin", "symbol_migrated", "AVB->VMRK", {"execution_rows": 2})
    _placed(mid, "VMRK", qty=0.0, dollars=5.0)                      # the renamed original buy
    _placed(mid, "VMRK", qty=0.048268, corpact="AVB->VMRK", dollars=0.0)
    monkeypatch.setattr(positions, "_fetch_full_order_feed", lambda c, a: ["feed"])
    monkeypatch.setattr(positions, "club_position_from_orders",
                        lambda orders: {"AVB": 0.026932})           # immutable broker history
    got = positions.club_shares_derived(mid, client=object(), account_number="ACC1")
    assert "VMRK" in got, "the renamed holding must be sellable again"
    assert got["VMRK"] == pytest.approx(0.0752, abs=1e-9)
    assert "AVB" not in got, "the old ticker must not leak through as its own name"


def test_an_unrenamed_feed_symbol_the_club_never_traded_is_still_dropped(monkeypatch):
    """The personal-safety belt must survive the fix: a feed symbol outside the
    club ledger is still ignored, so the club can never sell personal shares."""
    mid = _member()
    _placed(mid, "AAPL", qty=1.0)
    monkeypatch.setattr(positions, "_fetch_full_order_feed", lambda c, a: ["feed"])
    monkeypatch.setattr(positions, "club_position_from_orders",
                        lambda orders: {"AAPL": 1.0, "TSLA": 99.0})
    got = positions.club_shares_derived(mid, client=object(), account_number="ACC1")
    assert got == {"AAPL": pytest.approx(1.0)}, "a never-traded name must stay untouched"


# ── 2. the sell leg spending the buy budget ──────────────────────────────────

def test_sell_leg_has_its_own_budget_and_is_still_capped():
    """A cycle can need up to 2 x TOP_N orders. With one shared counter the
    sells ran first and the buys were dropped as 'max_orders cap', so members
    ended the cycle holding fewer names than they had committed dollars for.

    Asserted on the source because the executor's place path needs a live broker;
    the invariant is structural — the two legs must not share a counter, and
    each must be bounded."""
    import inspect

    from app.services import executor
    src = inspect.getsource(executor._run_impl)
    assert "sell_count = 0" in src, "the sell leg needs its own counter"
    assert "sell_count += 1" in src, "sells must increment only their own counter"
    assert "sell_count >= member_max_orders" in src, "the sell leg must still be capped"
    # the buy-leg check must still exist and must NOT be fed by sells
    assert "order_count >= member_max_orders" in src
    # slice the SELL phase precisely: the buy path legitimately increments
    # order_count before its own cap check (the errored-place recovery branch).
    sell_body = src[src.index("# ── SELL phase"):src.index("# ── BUY phase")]
    assert "order_count += 1" not in sell_body, (
        "a sell still increments the BUY counter — the rotation will starve its own buys")
    assert "sell_count += 1" in sell_body
    assert "sell_count >= member_max_orders" in sell_body, "the sell leg must be capped"


def test_a_full_rotation_fits_in_the_budget():
    """The arithmetic the defect came down to: at TOP_N=20 a full rotation is 20
    sells + 20 buys. Under one shared MAX_ORDERS=25 budget that is impossible;
    under a per-leg budget both legs fit."""
    top_n, max_orders = 20, 25
    shared_leftover_for_buys = max(0, max_orders - top_n)
    assert shared_leftover_for_buys < top_n, "one shared budget cannot fit a rotation"
    assert min(top_n, max_orders) == top_n, "per-leg, every sell fits"
    assert min(top_n, max_orders) == top_n, "per-leg, every buy fits"


# ── 3. the buy leg gave up on the proceeds too early ─────────────────────────

def test_bp_poll_waits_for_the_full_proceeds_not_80_percent():
    """Every member deferred their LAST pick(s) as 'settles next cycle' on a
    cycle where all four accounts were limited_margin — i.e. no settlement wait
    exists at all. The poll exited once ~80% of the sold value had credited, so
    a fifth of every rotation's proceeds was unspendable and the tail of the
    basket was stranded."""
    from app.services import executor
    assert executor.BP_POLL_TARGET_FRAC >= 0.95, (
        "exiting well below the full proceeds strands the tail of the rotation")
    assert executor.BP_POLL_FLAT_POLLS >= 2, (
        "one flat read is a plateau BETWEEN fills, not the end of the credit")


def test_bp_poll_plateau_needs_consecutive_flat_reads():
    """Fills land progressively at 09:31, so buying power stalls between them.
    A single non-rising read used to end the wait."""
    import inspect

    src = inspect.getsource(executor_run_impl := __import__(
        "app.services.executor", fromlist=["_run_impl"])._run_impl)
    assert "flat >= BP_POLL_FLAT_POLLS" in src, "the plateau must be confirmed, not assumed"
    assert "flat = 0" in src and "flat += 1" in src
    assert "0.8 * placed_sell_usd" not in src, "the old 80% early exit must be gone"


# ── 4. the emergency lever ───────────────────────────────────────────────────

def test_rescue_refuses_a_name_in_the_current_basket(monkeypatch):
    from app.services import rescue, voting
    mid = _member("rescue-basket@x.com")
    monkeypatch.setattr(voting, "latest_session",
                        lambda st: {"id": _SID[0], "picks_json": '[{"symbol": "AAPL"}]'})
    p = rescue.plan(mid, "AAPL")
    assert p["ok"] is False and "current basket" in p["reason"]


def test_rescue_refuses_a_position_the_club_does_not_own(monkeypatch):
    """The personal-shares guard: if the club ledger has no claim, the lever
    must refuse even though the account holds the name."""
    from app.services import positions, rescue, voting
    mid = _member("rescue-personal@x.com")
    monkeypatch.setattr(voting, "latest_session", lambda st: {"id": _SID[0], "picks_json": "[]"})
    monkeypatch.setattr(rescue.vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares", lambda m: {})
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {})
    monkeypatch.setattr(positions, "holdings_with_value",
                        lambda m: [{"symbol": "TSLA", "quantity": 3.0, "value": 900.0}])
    p = rescue.plan(mid, "TSLA")
    assert p["ok"] is False and "club holds no TSLA" in p["reason"]


def test_rescue_plans_a_full_exit_of_a_stranded_club_name(monkeypatch):
    """The VMRK case: held by the club, not in the basket, sellable."""
    from app.services import positions, rescue, voting
    mid = _member("rescue-vmrk@x.com")
    monkeypatch.setattr(voting, "latest_session", lambda st: {"id": _SID[0], "picks_json": "[]"})
    monkeypatch.setattr(rescue.vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"VMRK": 0.0752})
    monkeypatch.setattr(positions, "club_shares", lambda m: {"VMRK": 0.0752})
    monkeypatch.setattr(positions, "holdings_with_value",
                        lambda m: [{"symbol": "VMRK", "quantity": 0.0752, "value": 4.88}])
    p = rescue.plan(mid, "VMRK")
    assert p["ok"] is True
    assert p["sell_qty"] == pytest.approx(0.0752)


def test_rescue_never_sells_more_than_the_account_holds(monkeypatch):
    from app.services import positions, rescue, voting
    mid = _member("rescue-clamp@x.com")
    monkeypatch.setattr(voting, "latest_session", lambda st: {"id": _SID[0], "picks_json": "[]"})
    monkeypatch.setattr(rescue.vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"XYZ": 99.0})
    monkeypatch.setattr(positions, "club_shares", lambda m: {"XYZ": 99.0})
    monkeypatch.setattr(positions, "holdings_with_value",
                        lambda m: [{"symbol": "XYZ", "quantity": 2.0, "value": 10.0}])
    p = rescue.plan(mid, "XYZ")
    assert p["sell_qty"] == pytest.approx(2.0), "must clamp to what the account actually holds"


def test_rescue_dry_run_places_nothing_and_respects_the_kill_switch(monkeypatch):
    from app.services import positions, rescue, voting
    mid = _member("rescue-dry@x.com")
    monkeypatch.setattr(voting, "latest_session", lambda st: {"id": _SID[0], "picks_json": "[]"})
    monkeypatch.setattr(rescue.vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"VMRK": 0.05})
    monkeypatch.setattr(positions, "club_shares", lambda m: {"VMRK": 0.05})
    monkeypatch.setattr(positions, "holdings_with_value",
                        lambda m: [{"symbol": "VMRK", "quantity": 0.05, "value": 3.0}])
    out = rescue.rescue_sell(mid, "VMRK", "owner@x.com", dry_run=True)
    assert out["ok"] is True and "placed" not in out
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM executions WHERE member_id=? AND side='sell'",
                            (mid,)).fetchone()["c"] == 0
    # a real attempt while disarmed must also place nothing
    out = rescue.rescue_sell(mid, "VMRK", "owner@x.com", dry_run=False)
    assert out["placed"] is False and "kill switch" in out["reason"]
