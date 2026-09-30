"""Committed dollars never silently leave a slice (A3 / A4 buy paths, X3, X5).

Every buy the leg declines to place is club money the member committed and the
cycle did not spend. The only place it can survive is `member_settings.slice_carry`,
which the next cycle's compound_budget folds back into the valuation — so a buy
path that skips WITHOUT accruing carry is a silent, permanent shrink of that
member's slice. These tests pin the accrual for every reachable skip:

  * BP-deferred, review-exception, blocking-alert and max_orders-capped buys
  * a no-quote buy (full target when nothing is held, 0 when it is)
  * a mid-leg auth expiry (X3 — the remaining names carry, the write-back runs)
  * a carry credit reconcile posts AFTER the budget stamp (it is drained, once)

and the bp_left contract that decides which of those a LATER name can still
place (A3: only a reviewed-clean order consumes headroom).

TWO COLUMNS, TWO SEMANTICS — the rework these tests pin. Five writers used to
share member_settings.slice_carry, mixing the executor's per-cycle OVERWRITE
with asynchronous ADDITIVE credits, so a second run of the same session re-added
the first run's carry and the same dollars could be both re-deployed and
credited. They are now split:

  slice_carry    this cycle's OWN undeployed dollars. WRITTEN (never added to)
                 by the buy leg's write-back, so a re-run recomputes it from
                 scratch and it cannot accumulate.
  carry_pending  dollars an EARLIER cycle failed to deploy, proven AT THE BROKER
                 never to have landed. ADDITIVE, and reconcile_fills is its only
                 writer — every credit is marked on the execution row it came
                 from, so a row can fund at most one credit.

The write-back drains one into the other in a single transaction
(slice_carry = unplaced_usd + carry_pending; carry_pending = 0) and reports
msum["carry_drained"]. Retirement credits nothing: only reconcile reads the
broker, so only reconcile can prove an order never landed.

The carry write-back is gated on `will_place`, so every carry assertion here
runs an ARMED LIVE pass (fake broker, throwaway DB — no network, no money).

Run from backend/:  python -m pytest tests/test_audit_carry.py -q
"""
import contextlib
import datetime
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

from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_set, set_armed  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.robinhood.client import RHAuthError  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, reconcile, voting  # noqa: E402

# Rows this module created, so teardown can drop exactly those.
_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    # Every module shares one temp DB (CONCORD_DB_PATH via setdefault) and the
    # later ones run executor.run()/reconcile_all() over ALL members and
    # sessions. This file's trade dates are years out, so a session left behind
    # is the NEWEST ballot in the DB for every module that follows — and its
    # armed members are connected accounts those modules' runs would trade.
    # Drop exactly what this file made, never anything it did not.
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
            row = conn.execute("SELECT trade_date FROM vote_sessions WHERE id=?",
                               (sid,)).fetchone()
            if row:     # run_reports is keyed by trade date, not session id
                conn.execute("DELETE FROM run_reports WHERE trade_date=?",
                             (row["trade_date"],))
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))
            # per-cycle stamps, all keyed {prefix}:{sid}:{mid}
            for prefix in ("cycle_budget", "carry_drained", "repair_spend",
                           "strand_hold"):
                conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"{prefix}:{sid}:%",))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The A5 retry sleeps 2s between attempts — skip that in tests."""
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _stub_dust(monkeypatch):
    """The per-member sweep is not under test here and would place its own $1
    repairs on an armed run. The stub takes session_id=… (A7 widened the
    signature) so a miss shows up as a TypeError, not a silent skip."""
    monkeypatch.setattr(executor, "dust_cleanup",
                        lambda mid, dry_run=True, session_id=None: {"ok": True, "orders": []})


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, *, alloc_value: float = 50.0, alloc_type: str = "dollar",
            alloc_applied: float | None = None, slice_carry: float = 0.0,
            max_orders: int = 25, daily_cap_usd: float = 0.0,
            compound: int = 1) -> int:
    """A connected, opted-in member. Defaults to a COMPOUNDING DOLLAR stake —
    the only shape whose buy leg reaches the carry write-back at all."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,1,0,?,?,?,?,?)",
            (mid, daily_cap_usd, max_orders, alloc_type, alloc_value, int(time.time())),
        )
        # compound / alloc_applied / slice_carry arrive by migration — set them
        # explicitly so the budget path under test is the one we think it is.
        conn.execute(
            "UPDATE member_settings SET compound=?, alloc_applied=?, slice_carry=? "
            "WHERE member_id=?", (compound, alloc_applied, slice_carry, mid))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?,'connected')",
            (mid, f"ACC{mid}", b"enc-tokens", int(time.time()), int(time.time())),
        )
    _MEMBERS.append(mid)
    return mid


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    """A closed session whose basket is exactly `picks`, in alphabetical order.

    One voter weights every pick equally, so the tally's tiebreak (weight, votes,
    symbol) orders the basket by SYMBOL — which is what makes the buy leg's
    order-dependent branches (max_orders cap, bp_left drain) deterministic.
    TOP_N is widened first: the module default of 3 would truncate the basket.
    Trade dates are unique across the whole suite (the DB is shared between
    modules, and open_session_for hands back the EXISTING session for a date —
    a collision reopens someone else's closed ballot and raises).
    """
    object.__setattr__(settings, "top_n", max(settings.top_n, len(picks)))
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


def _hold(mid: int, symbol: str, qty: float, sid: int, *, aged_days: int = 0) -> None:
    """A prior cycle's placed club buy — the club-ledger side of a holding.
    Pair with a matching live position or the sell cap reads it as zero.

    `aged_days` backdates the row: the sell leg's PDT guard refuses to sell any
    name with a buy row created after the previous trading day's 16:00 ET, so a
    holding seeded at `now` reads as bought this morning and can never be exited.
    A full week clears the cutoff whatever weekday (and time of day) the suite
    runs at. Only the sell-leg tests need it."""
    executor._record(sid, mid, symbol, round(qty, 2), f"carry-seed-{sid}-{mid}-{symbol}",
                     "placed", place={"_concord_est_qty": qty})
    if aged_days:
        with db() as conn:
            conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND "
                         "member_id=? AND symbol=?",
                         (int(time.time()) - aged_days * 86400, sid, mid, symbol))


@contextlib.contextmanager
def _armed():
    """Run for real: the slice_carry write-back is gated on will_place, so a dry
    run proves nothing about whether a dollar survived. The basket floors are
    waived — these baskets are sized for exact arithmetic, not breadth (the
    conftest fixture restores settings either way)."""
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


def _carry(mid: int) -> float:
    """This cycle's own undeployed dollars (the executor's column)."""
    with db() as conn:
        return conn.execute("SELECT COALESCE(slice_carry,0) AS c FROM member_settings "
                            "WHERE member_id=?", (mid,)).fetchone()["c"]


def _pending(mid: int) -> float:
    """Dollars an EARLIER cycle failed to deploy, awaiting the next drain
    (reconcile's column — additive, and never written by the executor)."""
    with db() as conn:
        return conn.execute("SELECT COALESCE(carry_pending,0) AS c FROM member_settings "
                            "WHERE member_id=?", (mid,)).fetchone()["c"]


def _credit_pending(mid: int, amount: float) -> None:
    """Stand in for reconcile_fills proving an errored buy absent at the broker:
    the one thing it writes is an ADDITIVE carry_pending credit."""
    with db() as conn:
        conn.execute("UPDATE member_settings SET carry_pending=COALESCE(carry_pending,0)+? "
                     "WHERE member_id=?", (amount, mid))


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _order(summary: dict, mid: int, symbol: str, side: str = "buy") -> dict:
    return next(o for o in _mrow(summary, mid)["orders"]
                if o["symbol"] == symbol and o.get("side") == side)


def _exec_rows(sid: int, mid: int, symbol: str, side: str = "buy") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM executions WHERE session_id=? AND member_id=? AND symbol=? AND side=?",
            (sid, mid, symbol, side),
        ).fetchall()
    return [dict(r) for r in rows]


def _session_status(sid: int) -> str:
    with db() as conn:
        return conn.execute("SELECT status FROM vote_sessions WHERE id=?", (sid,)).fetchone()["status"]


class CarryClient:
    """Scripted broker double with per-symbol buy-leg outcomes.

    `review_exc` / `place_exc` (buys) and `sell_review_exc` (sells) raise for
    that symbol; `blocked` returns a real blocking alert; `on_review(acct,
    symbol)` fires before the buy review returns (used to simulate a credit
    landing mid-cycle). Records every call — assertions and hooks alike scope to
    one account, since the executor hands this same client to every member in the
    shared test DB.
    """

    def __init__(self, *, quotes=None, positions=None, buying_power=100.0,
                 total_value=1000.0, review_exc=None, place_exc=None,
                 sell_review_exc=None, blocked=(), on_review=None):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.buying_power = buying_power
        self.total_value = total_value
        self.review_exc = review_exc or {}
        self.place_exc = place_exc or {}
        self.sell_review_exc = sell_review_exc or {}
        self.blocked = set(blocked)
        self.on_review = on_review

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

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
        return {"data": {"orders": []}}

    def review_market_buy(self, acct, symbol, amt):
        self.calls.append(("review_buy", acct, symbol, amt))
        if self.on_review is not None:
            self.on_review(acct, symbol)
        if symbol in self.review_exc:
            raise self.review_exc[symbol]
        if symbol in self.blocked:
            return {"data": {"order_checks": {"alertType": "EQUITY_NOT_ENOUGH_BP"}}}
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, acct, symbol, amt, ref_id=None):
        self.calls.append(("place_buy", acct, symbol, amt, ref_id))
        if symbol in self.place_exc:
            raise self.place_exc[symbol]
        return {"data": {"order": {}}}

    def review_market_sell(self, acct, symbol, qty):
        self.calls.append(("review_sell", acct, symbol, qty))
        if symbol in self.sell_review_exc:
            raise self.sell_review_exc[symbol]
        return {"data": {"order_checks": {}}}

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.calls.append(("place_sell", acct, symbol, qty, ref_id))
        return {"data": {"order": {}}}

    def trades_for(self, kind: str, acct: str, symbol: str | None = None) -> list[tuple]:
        """This account's recorded calls (the shared client sees every member the
        executor touches — assertions must scope to one account)."""
        return [c for c in self.calls
                if c[0] == kind and c[1] == acct and (symbol is None or c[2] == symbol)]


def _use_client(monkeypatch, client: CarryClient) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda mid: client)


# ─── 1. every deferred buy in one cycle lands in slice_carry ─────────────────

def test_all_four_deferral_paths_sum_into_slice_carry(monkeypatch):
    """One buy leg, four different ways to not place, one carry figure.

    Basket (alphabetical, $1 quotes, budget $39 over 6 names → $6.50 each):
      A     review raises          → 'skipped', carries $6.50
      AAPL  blocking alert         → 'skipped', carries $6.50
      ABBV  beyond buying power    → 'skipped', carries $6.50
      ABT   already over target    → 'held',    carries nothing
      ACN   needs $1.50, affords it→ 'placed'   (the only order; max_orders=1)
      ADBE  max_orders cap reached → 'skipped', carries $6.50

    The persisted slice_carry must equal the SUM of the four needs ($26.00) —
    any path that skips without accruing is a dollar that left the slice for good.
    """
    mid = _member("carry-four@x.com", alloc_value=39.0, alloc_applied=35.0, max_orders=1)
    # A cash account defers a whole name rather than buying a partial, which
    # keeps the shortfall arithmetic exact (the partial path is A3 step 6).
    kv_set(f"acct_type:{mid}", "cash")
    picks = ["A", "AAPL", "ABBV", "ABT", "ACN", "ADBE"]
    closed = _closed_session(datetime.date(2028, 8, 1), mid, picks)
    sid = closed["id"]
    # prior cycle: ABT is way over target, ACN sits $1.50 under it
    _hold(mid, "ABT", 30.0, 98101)
    _hold(mid, "ACN", 5.0, 98101)
    client = CarryClient(quotes={s: 1.0 for s in picks},
                         positions=[("ABT", "30.0"), ("ACN", "5.0")],
                         buying_power=4.0, total_value=1000.0,
                         review_exc={"A": RuntimeError("MCP HTTP 500")},
                         blocked={"AAPL"})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    row = _mrow(summary, mid)
    assert row["budget"] == 39.0 and row["per_pick"] == 6.50
    assert _order(summary, mid, "A")["status"] == "skipped"
    assert _order(summary, mid, "AAPL")["status"] == "skipped"
    assert _order(summary, mid, "ABBV")["note"].startswith("short $2.50")
    assert _order(summary, mid, "ABT")["status"] == "held"
    assert _order(summary, mid, "ACN")["status"] == "placed"
    assert _order(summary, mid, "ADBE")["note"] == "max_orders cap"
    # exactly one order reached the broker, and it was the affordable top-up
    assert [c[2] for c in client.trades_for("place_buy", f"ACC{mid}")] == ["ACN"]
    assert _carry(mid) == pytest.approx(6.50 * 4)
    # ...and the four needs are individually on record, not just in the total
    assert _exec_rows(sid, mid, "A")[0]["status"] == "skipped"
    assert _exec_rows(sid, mid, "ADBE")[0]["error"] == "max_orders cap reached"


# ─── 2. review failures are 'skipped'; only a PLACE leaves 'error' ───────────

def test_review_exception_is_skipped_and_still_flips_the_session(monkeypatch):
    """A6/A3: nothing reached the broker, so a review that raises records
    'skipped', not 'error' — and the session's executed-flip (which defers on
    any surviving 'error' row so A4 can still recover a buy the broker took)
    completes normally. The dollars retry next cycle via slice_carry instead."""
    mid = _member("carry-reviewfail@x.com", alloc_value=50.0)
    closed = _closed_session(datetime.date(2028, 8, 2), mid, ["AAPL"])
    sid = closed["id"]
    client = CarryClient(quotes={"AAPL": 10.0}, buying_power=100.0,
                         review_exc={"AAPL": RuntimeError("MCP HTTP 502")})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    rows = _exec_rows(sid, mid, "AAPL")
    assert len(rows) == 1 and rows[0]["status"] == "skipped"
    assert rows[0]["error"].startswith("review failed:")
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert "executed_deferred" not in summary
    assert _session_status(sid) == "executed"
    assert _carry(mid) == pytest.approx(50.0)     # the whole undeployed budget


def test_place_exception_is_error_and_defers_the_flip(monkeypatch):
    """The other half of the contract: a PLACE that raises may have been
    accepted by the broker, so it stays 'error' (A4's evidence) and holds the
    session open for the next run's recovery pass. No carry — crediting dollars
    that may already be deployed would double-deploy them next cycle."""
    mid = _member("carry-placefail@x.com", alloc_value=50.0)
    closed = _closed_session(datetime.date(2028, 8, 3), mid, ["AAPL"])
    sid = closed["id"]
    client = CarryClient(quotes={"AAPL": 10.0}, buying_power=100.0,
                         place_exc={"AAPL": RuntimeError("connection reset")})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    rows = _exec_rows(sid, mid, "AAPL")
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert rows[0]["error"].startswith("place:")
    assert "error row(s)" in summary["executed_deferred"]
    assert _session_status(sid) == "closed"
    assert _carry(mid) == pytest.approx(0.0)


# ─── 3. no-quote buys: carry the target only when nothing is held ────────────

def test_no_quote_carries_full_target_only_when_nothing_is_held(monkeypatch):
    """A3 step 3. Without a quote the leg cannot size a top-up, so it skips —
    but the committed dollars behave differently either side of the holding:

      unheld name → the whole target never deployed → carry it in full
      held name   → most of the target is already IN the position → carry 0
                    (crediting it would inflate the slice on the next mark)

    Budget $30 over 3 names = $10 each; only A is quoted, so only A places.
    """
    mid = _member("carry-noquote@x.com", alloc_value=30.0)
    picks = ["A", "AAPL", "ABBV"]
    closed = _closed_session(datetime.date(2028, 8, 4), mid, picks)
    sid = closed["id"]
    # ABBV sits in the ACCOUNT but not in the club ledger — the only shape that
    # reaches the buy leg unpriced, since a club-ledger holding with no quote
    # fails the member closed at the club-valuation gate ('unquotable_holding').
    client = CarryClient(quotes={"A": 1.0}, positions=[("ABBV", "5.0")],
                         buying_power=100.0, total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _order(summary, mid, "A")["status"] == "placed"
    for sym in ("AAPL", "ABBV"):
        assert _order(summary, mid, sym)["note"] == "no quote"
        assert _exec_rows(sid, mid, sym)[0]["error"] == "no quote for sizing"
    assert _carry(mid) == pytest.approx(10.0)    # AAPL's target only, not ABBV's


# ─── 4. bp_left: only a reviewed-clean order consumes headroom ───────────────

def test_blocked_and_review_failed_names_leave_headroom_for_later_names(monkeypatch):
    """A3 step 9 — `bp_left -= need` moved BELOW the review + alert gates.

    Budget $30 over 3 names = $10 each, with exactly $10 of buying power. A is
    blocked and AAPL's review raises; neither touched the broker, so neither may
    spend headroom — ABBV must still place its full $10. (When the deduction sat
    above these gates, the last name in a stable basket order starved on money
    nothing had actually spent.)
    """
    mid = _member("carry-bpleft@x.com", alloc_value=30.0, alloc_applied=20.0)
    picks = ["A", "AAPL", "ABBV"]
    closed = _closed_session(datetime.date(2028, 8, 7), mid, picks)
    # a still-held dropped name supplies the capacity the $30 commitment needs;
    # the cash is deliberately only $10 (one name's worth)
    _hold(mid, "ADBE", 20.0, 98102)
    client = CarryClient(quotes={"A": 1.0, "AAPL": 1.0, "ABBV": 1.0, "ADBE": 1.0},
                         positions=[("ADBE", "20.0")], buying_power=10.0,
                         total_value=1000.0, blocked={"A"},
                         review_exc={"AAPL": RuntimeError("MCP HTTP 500")})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(summary, mid)["budget"] == 30.0
    assert _order(summary, mid, "A")["status"] == "skipped"
    assert _order(summary, mid, "AAPL")["status"] == "skipped"
    assert _order(summary, mid, "ABBV")["status"] == "placed"
    assert [c[2] for c in client.trades_for("place_buy", f"ACC{mid}")] == ["ABBV"]
    assert client.trades_for("place_buy", f"ACC{mid}", "ABBV")[0][3] == "10.00"
    assert _carry(mid) == pytest.approx(20.0)    # A + AAPL, both undeployed


def test_a_placed_buy_does_consume_headroom(monkeypatch):
    """The converse, so the test above cannot pass by never deducting at all:
    A's placed $10 spends the account's only $10, and AAPL then defers short —
    carrying its full target rather than over-ordering the account."""
    mid = _member("carry-bpspent@x.com", alloc_value=20.0, alloc_applied=10.0)
    kv_set(f"acct_type:{mid}", "cash")     # no partials — a clean whole-name defer
    picks = ["A", "AAPL"]
    closed = _closed_session(datetime.date(2028, 8, 8), mid, picks)
    _hold(mid, "ADBE", 10.0, 98103)
    client = CarryClient(quotes={"A": 1.0, "AAPL": 1.0, "ADBE": 1.0},
                         positions=[("ADBE", "10.0")], buying_power=10.0,
                         total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(summary, mid)["budget"] == 20.0
    assert _order(summary, mid, "A")["status"] == "placed"
    assert _order(summary, mid, "AAPL")["note"].startswith("short $10.00")
    assert _carry(mid) == pytest.approx(10.0)


# ─── 5. auth expiry mid-buy-leg (X3) ─────────────────────────────────────────

def test_auth_expiry_mid_buy_leg_carries_the_rest(monkeypatch):
    """X3: a token that dies mid-pass must not take the un-bought dollars with it.

    A places; AAPL's review raises RHAuthError twice (the one member-scoped
    re-grab counts as the retry), stranding the rest of the leg. The failing
    name AND every name the loop never reached accrue their targets, the member
    is NOT marked skipped — orders already went out this run, and a skip_reason
    would gate the write-back off and erase the carry — and the write-back runs.
    """
    mid = _member("carry-authstrand@x.com", alloc_value=30.0)
    picks = ["A", "AAPL", "ABBV"]
    closed = _closed_session(datetime.date(2028, 8, 9), mid, picks)
    client = CarryClient(quotes={s: 1.0 for s in picks}, buying_power=100.0,
                         total_value=1000.0,
                         review_exc={"AAPL": RHAuthError("MCP HTTP 401: unauthorized")})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    row = _mrow(summary, mid)
    assert row.get("auth_stranded") is True
    assert row.get("skip_reason") is None
    assert _order(summary, mid, "A")["status"] == "placed"
    assert "auth expired mid-pass" in _order(summary, mid, "AAPL")["note"]
    assert "auth expired mid-pass" in _order(summary, mid, "ABBV")["note"]
    # one re-grab + retry of the SAME call, then the strand
    assert len(client.trades_for("review_buy", f"ACC{mid}", "AAPL")) == 2
    assert not client.trades_for("review_buy", f"ACC{mid}", "ABBV")
    assert _carry(mid) == pytest.approx(20.0)   # AAPL + ABBV, $10 each


# ─── 6. a credit posted after the budget stamp is DRAINED by the write-back ──

def test_write_back_drains_a_carry_credit_posted_after_the_stamp(monkeypatch):
    """reconcile posts to carry_pending at any moment — including mid-leg, after
    this cycle's budget was already stamped. The stamped budget can never account
    for those dollars (it was sized before they existed), so the write-back has to
    fold them in on top of its OWN figure rather than either ignoring them or
    treating them as part of what this cycle declined to deploy.

    Here $5 lands during the first review — an errored buy from an earlier cycle
    that reconcile has just proven absent at the broker. AAPL is then blocked,
    carrying its own $10. The write-back must end at $15 with carry_pending
    emptied: the drain is what makes the credit fund the next cycle exactly once
    instead of evaporating under the overwrite (or riding forever in a column
    nobody clears).
    """
    mid = _member("carry-preserve@x.com", alloc_value=20.0)
    picks = ["A", "AAPL"]
    closed = _closed_session(datetime.date(2028, 8, 10), mid, picks)

    def _credit(acct: str, symbol: str, _mid: int = mid) -> None:
        # scoped to THIS member's account: the executor hands one client to
        # every member in the shared test DB, and each of them reviews "A" too
        if symbol != "A" or acct != f"ACC{_mid}":
            return
        _credit_pending(_mid, 5.0)

    client = CarryClient(quotes={"A": 1.0, "AAPL": 1.0}, buying_power=100.0,
                         total_value=1000.0, blocked={"AAPL"}, on_review=_credit)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _order(summary, mid, "A")["status"] == "placed"
    assert _order(summary, mid, "AAPL")["status"] == "skipped"
    assert _mrow(summary, mid)["carry_drained"] == pytest.approx(5.0)
    assert _carry(mid) == pytest.approx(15.0)   # $10 undeployed + the $5 credit
    assert _pending(mid) == pytest.approx(0.0), "drained, not left to fund twice"


# ─── 7. a re-run of the same session never ACCUMULATES carry ─────────────────

def test_second_run_of_the_same_session_does_not_accumulate_carry(monkeypatch):
    """THE regression this split exists for.

    slice_carry is this cycle's OWN undeployed dollars, so the write-back WRITES
    it — it never adds to it. A second run of the same session therefore has to
    recompute the figure from scratch: whatever run 1 left is not a debt run 2
    inherits, it is a stale reading of the very same cycle.

    Run 1 places A and defers AAPL on a blocking alert → carry $10. Run 2 of the
    SAME session finds A already placed and gets AAPL through → nothing is
    undeployed, so slice_carry must end at run 2's own figure, $0. Under the old
    additive write the $10 survived (and compounded on every further re-run) —
    dollars that were now sitting in the member's AAPL position, re-presented to
    the next cycle's compound_budget as if they were still cash.
    """
    mid = _member("carry-rerun@x.com", alloc_value=20.0)
    picks = ["A", "AAPL"]
    closed = _closed_session(datetime.date(2028, 8, 11), mid, picks)
    blocked = CarryClient(quotes={"A": 1.0, "AAPL": 1.0}, buying_power=100.0,
                          total_value=1000.0, blocked={"AAPL"})
    _use_client(monkeypatch, blocked)

    with _armed():
        run1 = executor.run(closed, force_dry_run=False, phase="buy")
        assert _order(run1, mid, "A")["status"] == "placed"
        assert _order(run1, mid, "AAPL")["status"] == "skipped"
        assert _carry(mid) == pytest.approx(10.0)

        # …the alert clears (a transient BP hold lifting) and the run re-fires
        clear = CarryClient(quotes={"A": 1.0, "AAPL": 1.0}, buying_power=100.0,
                            total_value=1000.0)
        _use_client(monkeypatch, clear)
        run2 = executor.run(closed, force_dry_run=False, phase="buy")

    assert _order(run2, mid, "A")["note"] == "already placed", "never re-bought"
    assert _order(run2, mid, "AAPL")["status"] == "placed"
    assert [c[2] for c in clear.trades_for("place_buy", f"ACC{mid}")] == ["AAPL"]
    assert _carry(mid) == pytest.approx(0.0), \
        "run 2's own figure — not run 1's $10 plus it"


def test_weekly_cadence_second_run_does_not_accumulate_carry(monkeypatch):
    """The same regression in the shape that actually fires in production: the
    live cadence runs Monday 'both' (exit the dropped names, enter the new ones)
    and then a second 'buy'-phase pass against the SAME session to top up what
    Monday's unsettled proceeds could not fund.

    Two runs, one cycle, one budget stamp — so the top-up pass recomputes
    slice_carry from its own leg, and Monday's deferral must not be added on top
    of it. (This is also the pass that the old shared column double-counted worst:
    it runs while reconcile is crediting the very same cycle.)
    """
    mid = _member("carry-weekly@x.com", alloc_value=20.0, alloc_applied=20.0)
    kv_set(f"acct_type:{mid}", "cash")    # settled-only BP: one quick look, no partials
    picks = ["A", "AAPL"]
    closed = _closed_session(datetime.date(2028, 8, 12), mid, picks)
    # a dropped name from the prior cycle, backdated past the PDT guard so the
    # Monday leg really does sell it — the club slice marks at $20, which is what
    # the compounding budget rides.
    _hold(mid, "ADBE", 20.0, 98104, aged_days=7)
    monday = CarryClient(quotes={"A": 1.0, "AAPL": 1.0, "ADBE": 1.0},
                         positions=[("ADBE", "20.0")], buying_power=100.0,
                         total_value=1000.0, blocked={"AAPL"})
    _use_client(monkeypatch, monday)

    with _armed():
        both = executor.run(closed, force_dry_run=False, phase="both")
        assert _order(both, mid, "ADBE", side="sell")["status"] == "placed"
        assert _mrow(both, mid)["budget"] == 20.0
        assert _carry(mid) == pytest.approx(10.0)    # AAPL's blocked half

        tuesday = CarryClient(quotes={"A": 1.0, "AAPL": 1.0, "ADBE": 1.0},
                              positions=[("ADBE", "0.0")], buying_power=100.0,
                              total_value=1000.0)
        _use_client(monkeypatch, tuesday)
        topup = executor.run(closed, force_dry_run=False, phase="buy")

    assert _order(topup, mid, "AAPL")["status"] == "placed"
    assert _carry(mid) == pytest.approx(0.0), \
        "the top-up pass writes its own figure over Monday's, never on top of it"


# ─── 8. a pending credit is drained exactly once ─────────────────────────────

def test_a_pending_credit_is_drained_exactly_once(monkeypatch):
    """carry_pending is ADDITIVE and reconcile-owned; the write-back is the only
    thing that empties it, and it does so exactly once — but "once" means once
    per DOLLAR, not once per run.

    slice_carry is rebuilt from scratch by every run of a session, so a drain
    that only moved the dollars across would be erased by the next run: the
    column had been zeroed, the stamped budget was sized before the credit
    existed, and the money would belong to no cycle at all. The drain is
    therefore remembered per session, and every run of the cycle rebuilds the
    same figure.

    AAPL is blocked on BOTH runs, so each leg's own undeployed figure is a steady
    $10 — any drift off that is the credit being lost or re-counted. $7 pending:
    run 1 ends at $17 and reports the drain, and run 2 must end at $17 too, with
    the pending column still empty (nothing new arrived to re-add).
    """
    mid = _member("carry-drainonce@x.com", alloc_value=20.0)
    picks = ["A", "AAPL"]
    closed = _closed_session(datetime.date(2028, 8, 13), mid, picks)
    _credit_pending(mid, 7.0)        # an earlier cycle's buy, proven absent
    client = CarryClient(quotes={"A": 1.0, "AAPL": 1.0}, buying_power=100.0,
                         total_value=1000.0, blocked={"AAPL"})
    _use_client(monkeypatch, client)

    with _armed():
        run1 = executor.run(closed, force_dry_run=False, phase="buy")
        assert _mrow(run1, mid)["carry_drained"] == pytest.approx(7.0)
        assert _carry(mid) == pytest.approx(17.0)     # $10 deferred + the $7
        assert _pending(mid) == pytest.approx(0.0)

        run2 = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(run2, mid)["carry_drained"] == pytest.approx(7.0), \
        "the same drain, rebuilt — not a second helping and not a loss"
    assert _carry(mid) == pytest.approx(17.0), "run 2's own $10 deferral + the same $7"
    assert _pending(mid) == pytest.approx(0.0)

    # A THIRD pass with a fresh credit adds only that credit on top.
    _credit_pending(mid, 2.0)
    with _armed():
        run3 = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(run3, mid)["carry_drained"] == pytest.approx(9.0)
    assert _carry(mid) == pytest.approx(19.0)
    assert _pending(mid) == pytest.approx(0.0)


# ─── 9. verified-absent + declined place carries the dollars ONCE ────────────

def test_verified_absent_error_row_is_superseded_and_carries_only_once(monkeypatch):
    """The double-spend the supersession point exists to prevent.

    A prior run errored on a buy, so an 'error' row survives as evidence: the
    broker may have taken that order. This run's A4 pass asks the broker and gets
    a VERIFIED ABSENT answer — and from that proof onward the dollars belong to
    THIS run, which either re-places them or carries them. So the row is
    superseded at the proof (not at the place call): here the re-place is then
    declined by a blocking alert, the dollars carry in slice_carry, and a later
    reconcile pass must find nothing left to credit.

    Had the row survived its own proof, reconcile would later age it out and
    credit carry_pending for the same $10 the write-back already carried — one
    undeployed dollar counted twice, which is exactly how the slice inflated.
    """
    mid = _member("carry-supersede@x.com", alloc_value=10.0)
    closed = _closed_session(datetime.date(2028, 8, 14), mid, ["AAPL"])
    sid = closed["id"]
    # The prior run's evidence: an errored buy, backdated past reconcile's 3-day
    # age-out window — so it is exactly the row the aging path WOULD credit $10
    # for, once the session goes terminal. That is the double count.
    executor._record(sid, mid, "AAPL", 10.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: connection reset")
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE session_id=? AND member_id=?",
                     (int(time.time()) - 4 * 86400, sid, mid))
    # an empty order feed IS the proof of absence (the club/personal boundary
    # means A4 only ever looks at agentic orders); the re-place is then declined.
    client = CarryClient(quotes={"AAPL": 1.0}, buying_power=100.0,
                         total_value=1000.0, blocked={"AAPL"})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    statuses = sorted(r["status"] for r in _exec_rows(sid, mid, "AAPL"))
    assert statuses == ["skipped", "superseded"], \
        "the proven-absent row is retired; the declined re-place is the new record"
    assert _order(summary, mid, "AAPL")["status"] == "skipped"
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert _carry(mid) == pytest.approx(10.0), "carried by THIS run"
    assert _pending(mid) == pytest.approx(0.0)

    # …and the dollars are not credited a second time from the other direction.
    # The session is terminal and the row is 4 days old — every precondition of
    # the aging credit is met — but reconcile only ever considers 'placed'/'error'
    # rows, and the proof already retired this one.
    assert _session_status(sid) == "executed"
    out = reconcile.reconcile_fills(mid, client=client, account_number=f"ACC{mid}")
    assert out["carry_credited"] == []
    assert _pending(mid) == pytest.approx(0.0)
    assert _carry(mid) == pytest.approx(10.0)


# ─── 10. the executed flip: stranded members hold it, skipped members don't ──

def test_sell_leg_auth_strand_keeps_the_session_open(monkeypatch):
    """A member whose token dies MID-PASS leaves the cycle genuinely unfinished:
    the sells that did go out have no matching buys, and the proceeds are sitting
    as cash. Flipping the session to 'executed' there would hide it from the next
    run's crash-continuation — the branch that reuses the interrupted cycle's
    budget stamp and redeploys exactly those proceeds — so the session stays
    'closed' and the run reports the deferral instead.

    The strand must land AFTER a real placement: two dropped names, exited in
    symbol order, and the token dies on the second. A member stranded before
    their first order is the other case entirely (below) — nothing is in flight,
    so nothing is owed and the flip must not wait."""
    mid = _member("carry-sellstrand@x.com", alloc_value=20.0, alloc_applied=20.0)
    closed = _closed_session(datetime.date(2028, 8, 15), mid, ["A"])
    sid = closed["id"]
    _hold(mid, "ADBE", 10.0, 98105, aged_days=7)        # exits cleanly…
    _hold(mid, "AMD", 10.0, 98105, aged_days=7)         # …then the token dies
    client = CarryClient(quotes={"A": 1.0, "ADBE": 1.0, "AMD": 1.0},
                         positions=[("ADBE", "10.0"), ("AMD", "10.0")],
                         buying_power=100.0, total_value=1000.0,
                         sell_review_exc={"AMD": RHAuthError("MCP HTTP 401: unauthorized")})
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    row = _mrow(summary, mid)
    assert row.get("auth_stranded") is True
    assert next(o for o in row["orders"]
                if o["symbol"] == "ADBE")["status"] == "placed", "something IS in flight"
    # one re-grab + retry of the SAME call, then the strand
    assert len(client.trades_for("review_sell", f"ACC{mid}", "AMD")) == 2
    assert "stranded mid-pass" in summary["executed_deferred"]
    assert _session_status(sid) == "closed", "the cycle is unfinished — not 'executed'"


def test_a_member_skipped_before_placing_does_not_hold_the_flip_open(monkeypatch):
    """The other half of that gate. A member skipped BEFORE placing anything has
    nothing outstanding — no half-done cycle, no proceeds in limbo — so they must
    never hold the session open. (Sessions used to wedge exactly this way: one
    unreachable member kept 'closed' forever, freezing every voter's ballot and
    feeding error rows to every reconcile pass.) Here the member's stake is below
    the $1 broker minimum with nothing club-held, so the budget block skips them
    outright and the session still flips."""
    mid = _member("carry-skipflip@x.com", alloc_value=0.0)
    closed = _closed_session(datetime.date(2028, 8, 16), mid, ["A"])
    sid = closed["id"]
    client = CarryClient(quotes={"A": 1.0}, buying_power=100.0, total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "no_stake"
    assert row.get("auth_stranded") is None
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert "executed_deferred" not in summary
    assert _session_status(sid) == "executed"


# ─── 7. the sub-$1 top-up the resize threshold does not reach ────────────────
# THE RATCHET (live, 2026-09-15). A member's committed stake fell $20.93 → $13.49
# over eleven trading days while ~$7 piled up as idle cash. Nobody edited it: the
# executor rewrote it every cycle.
#
# A name short of target has three possible fates:
#   short >= $1                      → the buy leg places a top-up
#   short >= _RESIZE_MIN_FRAC * tgt  → the sell leg exits it whole, buy leg re-enters
#   otherwise                        → `need < 1.00` → 'held' → continue
# The third branch returned WITHOUT accruing unplaced_usd, so those dollars were
# neither deployed, nor resized, nor carried. compound_budget then marked the
# slice positions-only, the write-back stamped that lower figure as the new
# committed stake, and the next cycle started from it. Monotonic, compounding,
# and worst exactly where per-name targets sit near $1 — i.e. the smallest
# stakes, whose members can least afford it.

def test_a_sub_minimum_topup_below_the_resize_threshold_stays_in_the_slice(monkeypatch):
    """AAPL is $0.50 under a $10.00 target — 5%, under _RESIZE_MIN_FRAC, and
    under the broker's $1 minimum. It cannot be topped up and will not be
    resized, so the ONLY place those dollars can survive is slice_carry."""
    # budget = club_value 9.50 + (30.50 - 20.00) = 20.00 over two names = $10.00
    mid = _member("carry-submin@x.com", alloc_value=20.0, alloc_applied=9.50)
    picks = ["AAPL", "ABT"]
    closed = _closed_session(datetime.date(2032, 3, 1), mid, picks)
    _hold(mid, "AAPL", 9.50, 98701)
    client = CarryClient(quotes={s: 1.0 for s in picks},
                         positions=[("AAPL", "9.50")],
                         buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    row = _mrow(summary, mid)
    assert row["budget"] == 20.0 and row["per_pick"] == 10.0
    aapl = _order(summary, mid, "AAPL")
    assert aapl["status"] == "held"
    assert "below the $1 minimum" in aapl["note"], aapl["note"]
    assert _order(summary, mid, "ABT")["status"] == "placed"
    # THE ASSERTION THIS BUG FAILED: the refused $0.50 must still be club money.
    assert _carry(mid) == pytest.approx(0.50)


def test_a_name_at_or_over_target_still_carries_nothing(monkeypatch):
    """The other side of the same branch, and the reason this cannot be fixed by
    carrying the whole `need < 1.00` case blindly. A position at or ABOVE target
    owes nothing — carrying here would top the slice up out of the member's own
    cash on any down move, which is exactly what compounding must never do."""
    # budget = club_value 10.50 + (29.50 - 20.00) = 20.00 over two names = $10.00
    mid = _member("carry-attarget@x.com", alloc_value=20.0, alloc_applied=10.50)
    picks = ["AAPL", "ABT"]
    closed = _closed_session(datetime.date(2032, 3, 2), mid, picks)
    _hold(mid, "AAPL", 10.50, 98702)
    client = CarryClient(quotes={s: 1.0 for s in picks},
                         positions=[("AAPL", "10.50")],
                         buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert _mrow(summary, mid)["per_pick"] == 10.0
    aapl = _order(summary, mid, "AAPL")
    assert aapl["status"] == "held" and aapl["note"].startswith("at target")
    assert _carry(mid) == pytest.approx(0.0)


def test_the_refused_topup_is_still_committed_on_the_next_cycle(monkeypatch):
    """The property the ratchet violated: a dollar the broker refused must still
    be in NEXT cycle's budget. Runs the leg, then feeds the persisted state back
    through compound_budget exactly as the following cycle would."""
    from app.services import alloc as alloc_svc

    mid = _member("carry-noratchet@x.com", alloc_value=20.0, alloc_applied=9.50)
    picks = ["AAPL", "ABT"]
    closed = _closed_session(datetime.date(2032, 3, 3), mid, picks)
    _hold(mid, "AAPL", 9.50, 98703)
    client = CarryClient(quotes={s: 1.0 for s in picks},
                         positions=[("AAPL", "9.50")],
                         buying_power=50.0, total_value=1000.0)
    _use_client(monkeypatch, client)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    with db() as conn:
        s = conn.execute("SELECT alloc_value, alloc_applied, COALESCE(slice_carry,0) AS c "
                         "FROM member_settings WHERE member_id=?", (mid,)).fetchone()
    # next cycle marks the slice: AAPL $9.50 still held + ABT's $10.00 just bought
    club_value_next = 19.50
    nxt = alloc_svc.compound_budget(s["alloc_value"], s["alloc_applied"],
                                    club_value_next, 1000.0, slice_carry=s["c"])
    assert nxt == pytest.approx(20.0), (
        f"the stake ratcheted to {nxt} — the $0.50 the broker refused left the slice")
