"""Regression coverage for the crumb/sell family (A4, A7, A8, A9 + X1/X2/X8).

A4 — the sell leg's PDT guard is keyed on TIME, not on a DUSTFIX ref: anything
     bought since the previous trading day's 16:00 ET close is never sold today.
     Seeded prior-cycle holdings must therefore be BACKDATED (see _backdate) or
     every exit reads as "bought this morning".
A7 — dust_cleanup keys every ref, guard and record to the EXECUTING session
     (never the newest ballot); an in-basket sub-$1 slice is a voted hold, not
     dust; and the exit sweep's dust bound is RELATIVE (≤2% of the position) as
     well as absolute, so a personal sub-$1 stake beside a ~$1.30 club slice
     survives.
A8 — the crumb repair's funding is reserved BEFORE the buy leg can spend buying
     power to ~$0, so the $1 top-up fires on exactly the fully-committed
     accounts crumbs accumulate on.
A9 — MIN_SELL_QTY is 1e-4: everything in [1e-5, 1e-4) routes to the $1 repair
     instead of a doomed order the broker rejects onto the member's phone.
X1/X8 — supersession is a BUY-side act only. An errored SELL row may hold a
     PARTIAL fill and is then the only record of those shares anywhere, so
     neither the sell leg nor the dust sweep ever supersedes one — reconcile
     resolves it against the broker instead. (The buy side supersedes at the A4
     VERIFIED-ABSENT proof, not at the place call: from that proof onward the
     dollars belong to this run, which either re-places or carries them, so the
     stale row must not survive to be credited again.) And a dust REVIEW failure
     records 'skipped', not 'error' (nothing reached the broker).

Run from backend/:  python -m pytest tests/test_audit_crumbs.py -q
(uses a throwaway temp DB; the Robinhood client + vault are mocked — no network.)
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

from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402

# Rows this module created, so teardown can drop exactly those.
_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    # Every module shares one temp DB (CONCORD_DB_PATH via setdefault) and the
    # later ones run executor.run() over ALL members and sessions. This file's
    # trade dates are years out — a session left behind is the NEWEST ballot in
    # the DB for every module that follows, and the open ballot below outranks
    # any of them. Drop exactly what this file made, never anything it did not.
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
    """The A5 retry and the both-day buying-power poll sleep — skip that here."""
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, opted_in: int = 1, alloc_type: str = "percent",
            alloc_value: float = 50.0, connect: bool = True,
            rh_status: str = "connected") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (mid, opted_in, 10.0, 500.0, 10, alloc_type, alloc_value, int(time.time())),
        )
        if connect:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
                "connected_at,last_refresh_at,status) VALUES(?,?,?,?,?,?,?)",
                (mid, f"ACC{mid}", 1, b"enc-tokens", int(time.time()), int(time.time()), rh_status),
            )
    _MEMBERS.append(mid)
    return mid


def _open_ballot(trade_date: datetime.date) -> dict:
    """An OPEN session — recorded for teardown like any other."""
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    return s


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    s = _open_ballot(trade_date)
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


def _backdate(mid: int, *symbols: str, days: int = 10) -> None:
    """Make seeded prior-cycle rows LOOK like prior cycles.

    executor._record stamps created_at=now, so a seeded holding reads as bought
    this morning and the sell leg's time-based PDT guard skips every exit with
    "bought today — PDT guard, exits next session". Real prior-cycle rows are
    days old; the fixture has to be too. The default clears a long weekend with
    room to spare — the guard's cutoff is the PREVIOUS TRADING day's 16:00 ET,
    so a run on a Monday evening puts "3 days ago" back inside the window.
    """
    ts = int(time.time()) - days * 86400
    with db() as conn:
        if symbols:
            marks = ",".join("?" * len(symbols))
            conn.execute(
                f"UPDATE executions SET created_at=? WHERE member_id=? AND symbol IN ({marks})",
                (ts, mid, *symbols))
        else:
            conn.execute("UPDATE executions SET created_at=? WHERE member_id=?", (ts, mid))


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _order(summary: dict, mid: int, symbol: str, side: str) -> dict | None:
    return next((o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == symbol and o.get("side") == side), None)


def _dust(summary: dict, mid: int, symbol: str) -> dict | None:
    return next((o for o in _mrow(summary, mid).get("dust", [])
                 if o["symbol"] == symbol), None)


def _rows_for_ref(ref_id: str) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM executions WHERE ref_id=?", (ref_id,)).fetchall()]


def _exec_rows(sid: int, mid: int, symbol: str, side: str = "buy") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM executions WHERE session_id=? AND member_id=? AND symbol=? AND side=?",
            (sid, mid, symbol, side),
        ).fetchall()
    return [dict(r) for r in rows]


class FakeClient:
    """Scripted RobinhoodClient double; records every call it receives."""

    def __init__(self, *, quotes=None, positions=None, buying_power=1000.0,
                 total_value=1000.0):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.buying_power = buying_power
        self.total_value = total_value

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
        """This account's recorded trade calls (the shared client serves every
        member the executor touches — assertions must scope to one account)."""
        return [c for c in self.calls
                if c[0] == kind and c[1] == acct and (symbol is None or c[2] == symbol)]


class SpendingClient(FakeClient):
    """Buying power that actually FALLS when a buy places — the only way a test
    can tell a reserved repair dollar from a leftover one. Kept per account: the
    shared client serves every member the run touches, so one ledger would let
    another module's member drain ours."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._bp: dict[str, float] = {}

    def bp_of(self, acct: str) -> float:
        return self._bp.setdefault(acct, self.buying_power)

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        return {"data": {"buying_power": {"buying_power": f"{self.bp_of(acct):.2f}"},
                         "total_value": str(self.total_value)}}

    def place_market_buy(self, acct, symbol, amt, ref_id=None):
        self._bp[acct] = self.bp_of(acct) - float(amt)
        return super().place_market_buy(acct, symbol, amt, ref_id=ref_id)


class SweepClient(FakeClient):
    """Records the two sweep legs separately — a residue that reaches the SELL
    review at all is the defect MIN_SELL_QTY exists to prevent."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.buy_reviews: list[tuple] = []
        self.sell_reviews: list[tuple] = []

    def review_market_buy(self, acct, symbol, amt):
        self.buy_reviews.append((symbol, amt))
        return super().review_market_buy(acct, symbol, amt)

    def review_market_sell(self, acct, symbol, qty):
        self.sell_reviews.append((symbol, qty))
        return super().review_market_sell(acct, symbol, qty)


def _use_client(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda mid: client)


@contextlib.contextmanager
def _armed_live():
    """Live + ARMED, with the thin-basket floors waived: these tests deliberately
    trade a 1-2 name basket at TOP_N=3 (conftest restores settings regardless)."""
    from app.config import settings
    from app.db import set_armed

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


# ─── A8: the repair's funding survives a buy leg that spends everything ──────

def test_repair_fires_for_a_fully_committed_member(monkeypatch):
    """The whole point of the reservation (triage [3]): crumbs accumulate on
    accounts that deploy 100% of their stake, and on exactly those accounts the
    buy leg used to leave $0 behind — so the sweep's $1 top-up was skipped
    "buying power too low to repair" every single run, forever.

    The buy leg now sizes against buying power MINUS the repair reserve
    (_REPAIR_MAX_PER_RUN-bounded top-ups plus the _REPAIR_MIN_BP headroom), so
    the crumb is repaired on the same run that drains the rest of the account.
    """
    mid = _member("crumb-reserve@x.com", alloc_type="percent", alloc_value=100.0)
    # a prior cycle left a 5e-5 NVDA crumb — club-traded, now off the basket
    executor._record(90801, mid, "NVDA", 1.00, "reserve-crumb-ref", "placed",
                     place={"_concord_est_qty": 0.00005})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 1), mid, ["AAPL"])
    client = SpendingClient(quotes={"AAPL": 100.0, "NVDA": 250.0},
                            positions=[("NVDA", "0.00005")],
                            buying_power=20.0, total_value=20.0)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    # the buy leg spent everything it was ALLOWED to: $20 budget, $2 held back
    buy = _order(summary, mid, "AAPL", "buy")
    assert buy["status"] == "placed" and buy["amount"] == "18.00", buy
    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL")
    # ...and the reserved dollars funded the repair rather than the last buy
    fix = _dust(summary, mid, "NVDA")
    assert fix is not None and fix["side"] == "buy"
    assert fix["status"] == "placed", fix        # regression: "buying power too low"
    assert fix["amount"] == "1.00"
    assert client.trades_for("place_buy", f"ACC{mid}", "NVDA")
    # the crumb itself is never handed to the broker as a sell
    assert client.trades_for("review_sell", f"ACC{mid}", "NVDA") == []


# ─── A7: dust_cleanup is keyed to the EXECUTING session ─────────────────────

def test_dust_repair_keys_to_the_executing_session(monkeypatch):
    """run() hands dust_cleanup its own sid, so every ref/guard/record belongs to
    the session that is actually trading — not the newest decided session and
    never an open ballot, whose refs would disagree with run()'s and let the
    same repair fire again the next day.

    Also the same-day re-run contract: the DUSTFIX row is the idempotency state,
    so a second run of the same session repairs nothing.
    """
    mid = _member("crumb-sid@x.com")
    executor._record(90802, mid, "NVDA", 1.00, "sid-crumb-ref", "placed",
                     place={"_concord_est_qty": 0.00005})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 2), mid, ["AAPL"])
    sid = closed["id"]
    # a NEWER decided session (different basket) and a newer OPEN ballot: both
    # would out-rank the executing session under a MAX(id) anchor
    newer = _closed_session(datetime.date(2030, 6, 3), mid, ["MSFT"])
    ballot = _open_ballot(datetime.date(2030, 6, 4))
    client = FakeClient(quotes={"AAPL": 100.0, "MSFT": 100.0, "NVDA": 250.0},
                        positions=[("NVDA", "0.00005")],
                        buying_power=100.0, total_value=100.0)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    assert _dust(summary, mid, "NVDA")["status"] == "placed"
    fix_rows = _rows_for_ref(executor.ref_id_for(sid, mid, "DUSTFIX:NVDA"))
    assert len(fix_rows) == 1 and fix_rows[0]["status"] == "placed"
    assert fix_rows[0]["session_id"] == sid
    assert _rows_for_ref(executor.ref_id_for(newer["id"], mid, "DUSTFIX:NVDA")) == []
    assert _rows_for_ref(executor.ref_id_for(ballot["id"], mid, "DUSTFIX:NVDA")) == []

    # same-day re-run: nothing repaired twice, and the repaired position is not
    # sold today either (it is a placed buy row — the PDT guard sees it). The
    # morning's AAPL buy is in the account by now — a static fake that still
    # showed nothing deployed would read, correctly, as a slice the club lost
    # sight of and the mark gate would skip the member.
    client.positions_list = [("NVDA", "0.00005"), ("AAPL", "0.5")]
    with _armed_live():
        again = executor.run(closed, force_dry_run=False, phase="both")

    assert _dust(again, mid, "NVDA")["status"] == "skipped"
    assert "already attempted" in _dust(again, mid, "NVDA")["note"]
    assert len(_rows_for_ref(executor.ref_id_for(sid, mid, "DUSTFIX:NVDA"))) == 1
    sell = _order(again, mid, "NVDA", "sell")
    assert sell["status"] == "skipped" and "PDT guard" in sell["note"]
    assert client.trades_for("place_buy", f"ACC{mid}", "NVDA") == \
        client.trades_for("place_buy", f"ACC{mid}", "NVDA")[:1]


def test_basket_guard_reads_the_executing_sessions_basket(monkeypatch):
    """The repair's in-basket skip is resolved against the EXECUTING session's
    picks: a crumb on a name this cycle owns is absorbed by the member's own
    top-up, so topping it up again would double-buy. Under a newest-decided
    anchor the same name reads as off-basket and gets a redundant $1 repair.
    """
    mid = _member("crumb-basket-sid@x.com")
    executor._record(90803, mid, "NVDA", 1.00, "basket-crumb-ref", "placed",
                     place={"_concord_est_qty": 0.00005})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 5), mid, ["AAPL", "NVDA"])
    sid = closed["id"]
    _closed_session(datetime.date(2030, 6, 6), mid, ["MSFT"])   # newer, NVDA-free
    client = FakeClient(quotes={"AAPL": 100.0, "MSFT": 100.0, "NVDA": 250.0},
                        positions=[("NVDA", "0.00005")],
                        buying_power=12.0, total_value=20.0)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    assert _order(summary, mid, "NVDA", "buy")["status"] == "placed"   # the absorb
    fix = _dust(summary, mid, "NVDA")
    assert fix["status"] == "skipped"
    assert "buy leg absorbs" in fix["note"]
    assert _rows_for_ref(executor.ref_id_for(sid, mid, "DUSTFIX:NVDA")) == []


def test_in_basket_crumb_is_left_alone_when_the_members_buy_errored(monkeypatch):
    """The absorb guard counts ANY own-buy row for the session, not just a clean
    one. An errored or deferred basket buy is still the buy leg's name to
    finish, and repairing it there does real damage twice over: the repair's
    supersession would retire that buy's evidence, and the $1 DUSTFIX row —
    recorded as a PLACED buy of the same symbol — becomes an "already placed"
    key that suppresses the real buy on the next run of the session.
    """
    mid = _member("inbasket-errored-buy@x.com")
    executor._record(90810, mid, "NVDA", 1.00, "errored-buy-crumb-ref", "placed",
                     place={"_concord_est_qty": 0.00005})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 17), mid, ["NVDA"])
    sid = closed["id"]
    # this cycle's own basket buy of NVDA reached the broker and errored
    buy_ref = executor.ref_id_for(sid, mid, "NVDA")
    executor._record(sid, mid, "NVDA", 12.00, buy_ref, "error", error="place: timeout")
    client = SweepClient(quotes={"NVDA": 250.0}, positions=[("NVDA", "0.00005")],
                         buying_power=50.0)
    _use_client(monkeypatch, client)

    out = executor.dust_cleanup(mid, dry_run=True, session_id=sid)

    entry = next(o for o in out["orders"] if o["symbol"] == "NVDA")
    assert entry["status"] == "skipped"
    assert "buy leg absorbs" in entry["note"]
    assert client.buy_reviews == [], "no $1 repair on a name the buy leg owns"
    assert _rows_for_ref(buy_ref)[0]["status"] == "error", "the buy's evidence survives"
    assert _rows_for_ref(executor.ref_id_for(sid, mid, "DUSTFIX:NVDA")) == []


# ─── A7/A9: an in-basket sub-$1 slice is a voted hold, not dust ─────────────

def test_in_basket_sub_dollar_position_is_never_dust_sold(monkeypatch):
    """A small-stake member's full-target position on a high-priced name IS
    sub-$1 and sub-0.01-share — identical in shape to a crumb. The cycle owns
    it, so the sweep must leave it entirely alone (the resize path below is what
    corrects it)."""
    mid = _member("inbasket-dust@x.com")
    executor._record(90804, mid, "NVDA", 1.00, "inbasket-dust-ref", "placed",
                     place={"_concord_est_qty": 0.003})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 7), mid, ["NVDA"])
    client = SweepClient(quotes={"NVDA": 250.0}, positions=[("NVDA", "0.003")])
    _use_client(monkeypatch, client)

    out = executor.dust_cleanup(mid, dry_run=True, session_id=closed["id"])

    entry = next(o for o in out["orders"] if o["symbol"] == "NVDA")
    assert entry["side"] == "sell" and entry["status"] == "skipped"
    assert entry["note"] == "in-basket position — the cycle owns it"
    assert client.sell_reviews == [] and client.buy_reviews == []


def test_resize_reaches_an_in_basket_position_below_a_dollar(monkeypatch):
    """Triage [21]: the resize used to require `held_qty * price >= 1.00`, which
    made an in-basket position that had drifted UNDER $1 unreachable by every
    convergence path — too small to trim, its top-up too small to place. The
    clause is gone, so a $0.80 holding against a $1.05 target exits whole and
    the buy leg re-enters at target (basket_targets guarantees ≥ $1)."""
    mid = _member("resize-sub-dollar@x.com", alloc_type="percent", alloc_value=35.0)
    executor._record(90805, mid, "AAPL", 0.80, "resize-sub-ref", "placed",
                     place={"_concord_est_qty": 0.80})
    _backdate(mid, "AAPL")
    closed = _closed_session(datetime.date(2030, 6, 8), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 1.00}, positions=[("AAPL", "0.80")],
                        buying_power=2.20, total_value=3.00)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    sell = _order(summary, mid, "AAPL", "sell")
    assert sell["status"] == "placed" and sell["qty"] == "0.800000"
    assert "resize" in sell["note"], sell
    buy = _order(summary, mid, "AAPL", "buy")
    assert buy["status"] == "placed" and float(buy["amount"]) >= 1.00, buy


def test_reachable_top_up_is_a_plain_buy_not_a_resize(monkeypatch):
    """The resize band is bounded on BOTH sides: a shortfall of $1 or more is
    placeable as an ordinary top-up, so the position must not be round-tripped
    to close it."""
    mid = _member("resize-not-needed@x.com", alloc_type="percent", alloc_value=50.0)
    executor._record(90806, mid, "AAPL", 1.00, "resize-plain-ref", "placed",
                     place={"_concord_est_qty": 1.00})
    _backdate(mid, "AAPL")
    closed = _closed_session(datetime.date(2030, 6, 9), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 1.00}, positions=[("AAPL", "1.00")],
                        buying_power=4.00, total_value=5.00)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="both")

    assert _order(summary, mid, "AAPL", "sell") is None
    assert client.trades_for("review_sell", f"ACC{mid}", "AAPL") == []
    buy = _order(summary, mid, "AAPL", "buy")
    assert buy["status"] == "placed" and buy["amount"] == "1.50", buy


# ─── A9: MIN_SELL_QTY = 1e-4 ────────────────────────────────────────────────

def test_min_sell_qty_routes_a_5e_5_residue_to_the_repair(monkeypatch):
    """Broker rejections were observed up to 8e-6 sh and the smallest proven
    fill is 3.33e-4, so the floor moved 1e-5 → 1e-4. A 5e-5 residue — sellable
    under the old floor, rejected in practice — must never reach the broker; it
    routes to the $1 top-up instead. A residue ABOVE the floor still sells."""
    assert executor.MIN_SELL_QTY == 0.0001

    mid = _member("min-sell-qty@x.com")
    executor._record(90807, mid, "NVDA", 1.00, "minqty-ref", "placed",
                     place={"_concord_est_qty": 0.00005})
    _backdate(mid, "NVDA")
    closed = _closed_session(datetime.date(2030, 6, 10), mid, ["MSFT"])
    sid = closed["id"]
    client = SweepClient(quotes={"MSFT": 100.0, "NVDA": 254.0},
                         positions=[("NVDA", "0.00005")], buying_power=100.0)
    _use_client(monkeypatch, client)

    out = executor.dust_cleanup(mid, dry_run=True, session_id=sid)

    entry = next(o for o in out["orders"] if o["symbol"] == "NVDA")
    assert entry["side"] == "buy" and entry["status"] == "reviewed"
    assert entry["amount"] == "1.00"
    assert client.sell_reviews == [], "a sub-minimum residue must never be sell-reviewed"
    assert client.buy_reviews == [("NVDA", "1.00")]

    # ...and 1.2e-4 — just over the floor — is ordinary dust and does sell
    client.positions_list = [("NVDA", "0.00012")]
    out2 = executor.dust_cleanup(mid, dry_run=True, session_id=sid)
    entry2 = next(o for o in out2["orders"] if o["symbol"] == "NVDA")
    assert entry2["side"] == "sell" and entry2["status"] == "reviewed"
    assert client.sell_reviews == [("NVDA", "0.000120")]


# ─── A7: the exit sweep's relative dust bound ───────────────────────────────

def test_exit_sweeps_drift_scale_residue_above_the_absolute_floor(monkeypatch):
    """Est-vs-fill residue is a fraction of a percent of the position, so a
    0.015-share crumb on a 1.0-share exit (well over the 0.001 absolute floor)
    is still swept — the position closes clean."""
    mid = _member("sweep-drift@x.com")
    executor._record(90808, mid, "TSLA", 50.0, "sweep-drift-ref", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(mid, "TSLA")
    closed = _closed_session(datetime.date(2030, 6, 11), mid, ["MSFT"])  # TSLA dropped
    client = FakeClient(quotes={"MSFT": 100.0, "TSLA": 50.0},
                        positions=[("TSLA", "1.015")],
                        buying_power=200.0, total_value=200.0)
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    sells = client.trades_for("review_sell", f"ACC{mid}", "TSLA")
    assert len(sells) == 1 and sells[0][3] == "1.015000", sells
    assert "dust swept" in _order(summary, mid, "TSLA", "sell")["note"]


def test_exit_leaves_a_personal_stake_large_relative_to_the_club_slice(monkeypatch):
    """Triage [64]: a value-only bound ("residual worth ≤ $1") swept a member's
    own sub-$1 stake whenever the club slice itself was ~$1.30 — the personal
    shares were half the position and still counted as dust. The sweep now also
    requires the residue to be ≤2% of what the club is selling, so only genuine
    drift is absorbed."""
    mid = _member("sweep-personal@x.com")
    executor._record(90809, mid, "TSLA", 1.30, "sweep-personal-ref", "placed",
                     place={"_concord_est_qty": 1.30})
    _backdate(mid, "TSLA")
    closed = _closed_session(datetime.date(2030, 6, 12), mid, ["MSFT"])
    # $1.30 club slice + a $0.50 personal stake at $1.00/share
    client = FakeClient(quotes={"MSFT": 100.0, "TSLA": 1.00},
                        positions=[("TSLA", "1.80")],
                        buying_power=100.0, total_value=100.0)
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    sells = client.trades_for("review_sell", f"ACC{mid}", "TSLA")
    assert len(sells) == 1 and sells[0][3] == "1.300000", sells
    assert _order(summary, mid, "TSLA", "sell")["note"] == "exited basket"


# ─── X1/X8: sells are never superseded; dust review failures are 'skipped' ───

def test_dust_sell_leaves_a_prior_errored_sell_standing(monkeypatch):
    """X1: a dust sell NEVER supersedes an errored sell — not on review, and not
    on the place call either.

    The asymmetry with the buy side is the whole point. An errored BUY is
    all-or-nothing: the A4 verification proves at the broker that the order is
    VERIFIED-ABSENT before this run re-places it, and from that proof onward the
    dollars belong to this run, so the stale row must not survive to be credited
    a second time. An errored SELL has no such proof available: the order may
    have PARTIALLY filled before the write failed, and that row is then the only
    record anywhere of the shares that did leave. Superseding it would erase
    them from the club ledger while the member's account has already lost them —
    a silent, unrecoverable shortfall, and the dust sell is not even selling the
    same quantity.

    So the row stays 'error' and reconcile_fills resolves it against the broker
    instead: it promotes the row to 'placed' at the real fill if the order
    landed, demotes/retires it if the broker positively killed it, and leaves it
    alone while it cannot tell. Evidence is never destroyed by a writer that
    cannot read the broker.
    """
    mid = _member("dust-supersede@x.com")
    executor._record(90810, mid, "TSLA", 1.00, "dust-sup-ref", "placed",
                     place={"_concord_est_qty": 0.003})
    closed = _closed_session(datetime.date(2030, 6, 13), mid, ["MSFT"])
    sid = closed["id"]
    # this session's sell leg already errored on TSLA
    executor._record(sid, mid, "TSLA", None, executor.ref_id_for(sid, mid, "SELL:TSLA"),
                     "error", error="sell: connection reset", side="sell")
    _backdate(mid, "TSLA")
    client = FakeClient(quotes={"MSFT": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "0.003")], buying_power=100.0)
    _use_client(monkeypatch, client)

    preview = executor.dust_cleanup(mid, dry_run=True, session_id=sid)

    assert next(o for o in preview["orders"] if o["symbol"] == "TSLA")["status"] == "reviewed"
    errs = _exec_rows(sid, mid, "TSLA", "sell")
    assert [r["status"] for r in errs] == ["error"], "a dry run mutates nothing at all"

    with _armed_live():
        live = executor.dust_cleanup(mid, dry_run=False, session_id=sid)

    assert next(o for o in live["orders"] if o["symbol"] == "TSLA")["status"] == "placed"
    rows = {r["ref_id"]: r for r in _exec_rows(sid, mid, "TSLA", "sell")}
    err = rows[executor.ref_id_for(sid, mid, "SELL:TSLA")]
    assert err["status"] == "error", "a possible partial fill is not evidence to throw away"
    assert err["error"] == "sell: connection reset", "with its text intact for reconcile"
    assert rows[executor.ref_id_for(sid, mid, "DUST:TSLA")]["status"] == "placed"
    assert client.trades_for("place_sell", f"ACC{mid}", "TSLA")


def test_sell_leg_leaves_a_prior_errored_sell_standing(monkeypatch):
    """X1, the other half: the SELL LEG itself never supersedes an errored sell.

    The dust sweep is not the only writer that re-sells a name a prior run
    errored on — the sell leg does it every time a crash-continued session
    re-exits a dropped holding. The contract is the same, and for the same
    reason: an errored SELL may have PARTIALLY filled before the write failed,
    and that row is then the only record anywhere of the shares that did leave.
    Retiring it would erase them from the club ledger while the account has
    already lost them.

    So this run places its exit and the 'error' row stays 'error', text intact.
    Both rows carry the SAME ref_id (the sell ref is keyed to session+symbol, and
    the broker's agentic feed returns ref_id=null anyway), which is precisely the
    ambiguity reconcile_fills is built to resolve at the broker: 'placed' rows
    claim a matching order first, so the new order binds to the new row and the
    error row is promoted only on an order of its own — or demoted/retired when
    the broker says it positively died.

    Contrast the buy side, which DOES supersede — but at the A4 VERIFIED-ABSENT
    proof, never at the place call: a buy is all-or-nothing, so once the broker
    has said the order is not there the dollars belong to this run and the stale
    row must not survive to be credited again.
    """
    mid = _member("sell-leg-supersede@x.com")
    # a prior cycle's holding of a name THIS ballot dropped → the sell leg exits it
    executor._record(90812, mid, "TSLA", 250.0, "sellleg-hold-ref", "placed",
                     place={"_concord_est_qty": 1.0})
    closed = _closed_session(datetime.date(2030, 6, 20), mid, ["MSFT"])
    sid = closed["id"]
    _backdate(mid, "TSLA")          # the HOLDING is old; the error row below is not
    # this session's first run already errored on that exit, minutes ago — the
    # crash-continuation shape. Left at created_at=now on purpose: backdating it
    # too would put it past reconcile's 3-day age-out window, and the retirement
    # pass run() opens with would skip it before the sell leg ever saw it.
    executor._record(sid, mid, "TSLA", None, executor.ref_id_for(sid, mid, "SELL:TSLA"),
                     "error", error="sell: connection reset", side="sell")
    client = FakeClient(quotes={"MSFT": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "1.0")], buying_power=100.0)
    _use_client(monkeypatch, client)

    with _armed_live():
        summary = executor.run(closed, force_dry_run=False, phase="sell")

    assert _order(summary, mid, "TSLA", "sell")["status"] == "placed"
    assert client.trades_for("place_sell", f"ACC{mid}", "TSLA")
    rows = _exec_rows(sid, mid, "TSLA", "sell")
    assert sorted(r["status"] for r in rows) == ["error", "placed"], \
        "the re-exit is a NEW row beside the evidence, never a replacement for it"
    err = next(r for r in rows if r["status"] == "error")
    assert err["error"] == "sell: connection reset", "with its text intact for reconcile"
    assert all(r["status"] != "superseded" for r in rows)


def test_dust_review_failure_records_skipped_not_error(monkeypatch):
    """X8a: nothing reached the broker, so there is no fill to recover — and an
    'error' row would hold the session's executed flip open on exactly the
    exited/halted names dust review failures cluster on."""
    mid = _member("dust-review-fail@x.com")
    executor._record(90811, mid, "TSLA", 1.00, "dust-revfail-ref", "placed",
                     place={"_concord_est_qty": 0.003})
    closed = _closed_session(datetime.date(2030, 6, 14), mid, ["MSFT"])
    sid = closed["id"]
    _backdate(mid, "TSLA")

    class FlakyClient(FakeClient):
        def review_market_sell(self, acct, symbol, qty):
            raise RuntimeError("MCP HTTP 502: bad gateway")

    client = FlakyClient(quotes={"MSFT": 100.0, "TSLA": 250.0},
                         positions=[("TSLA", "0.003")], buying_power=100.0)
    _use_client(monkeypatch, client)

    out = executor.dust_cleanup(mid, dry_run=False, session_id=sid)

    entry = next(o for o in out["orders"] if o["symbol"] == "TSLA")
    assert entry["status"] == "skipped" and "502" in entry["note"]
    rows = _exec_rows(sid, mid, "TSLA", "sell")
    assert len(rows) == 1 and rows[0]["status"] == "skipped"
    assert rows[0]["ref_id"] == executor.ref_id_for(sid, mid, "DUST:TSLA")
    assert "dust review failed" in (rows[0]["error"] or "")
