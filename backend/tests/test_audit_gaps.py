"""Gaps a revert-check audit found: contracts the batch states but no test held.

Each test here was written because deliberately BREAKING the thing it describes
left the rest of the suite green. They are the cases that only appear on a LIVE
armed pass, or only across sessions, or only when a second guard is not standing
in for the first.

A4 (the dangerous one) — every existing duplicate-buy test runs force_dry_run,
    so `if will_place: _supersede_error(...)` was dead code in all of them and
    the ORDERING rule it encodes went unverified. Superseding a prior errored buy
    before the broker has been asked is the mistake that destroys the only
    evidence an order may have landed; it may happen strictly AFTER the
    verified-absent proof, and only when an order is really going out.
PDT — the sell leg's guard is keyed on TIME across ALL sessions, not on this
    session's rows: yesterday's buy belongs to yesterday's session.
BUDGET — a strict total_value read is its own guard. The club-value consistency
    check shadows it for a member holding stock, so the uncovered case is the
    dangerous one: a member holding little or nothing, where a shape-shifted
    payload reads as a real, tiny account.
STRAND — a member stranded before placing anything must NOT hold the executed
    flip; sessions used to wedge exactly that way.
FEED — A4 read only PAGE ONE of the broker's order feed and treated "not found"
    as proof the order never landed. The feed is fetched from inside the buy
    loop, after the sell leg and this run's earlier buys, so the run's own
    orders crowd page one ahead of the older row being verified.
NaN — a non-finite quote is truthy and is not None, so it slipped through both
    quote filters and then walked past the fail-closed valuation guard.

Run from backend/:  python -m pytest tests/test_audit_gaps.py -q
(throwaway temp DB; the Robinhood client + vault are mocked — no network, and
every "live" pass here is armed against that fake broker.)
"""
import contextlib
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

from app.config import settings  # noqa: E402
from app.db import db, init_db, set_armed  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402

_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    """The temp DB is shared across modules — drop exactly what this one made,
    including the per-cycle kv the executor stamps (the same family its own GC
    lists) and the run_reports every executor.run() leaves behind."""
    with db() as conn:
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT trade_date FROM vote_sessions WHERE id IN (%s)"
            % ",".join("?" * len(_SESSIONS)) if _SESSIONS else
            "SELECT trade_date FROM vote_sessions WHERE 0", _SESSIONS).fetchall()] if _SESSIONS else []
        for mid in _MEMBERS:
            conn.execute("DELETE FROM executions WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM votes WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
            conn.execute("DELETE FROM kv WHERE key IN (?,?)",
                         (f"clamp_pending:{mid}", f"acct_type:{mid}"))
        for td in dates:
            conn.execute("DELETE FROM run_reports WHERE trade_date=?", (td,))
        for sid in _SESSIONS:
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))
            for pfx in ("cycle_budget", "carry_drained", "repair_spend", "strand_hold"):
                conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"{pfx}:{sid}:%",))


# ─── helpers ─────────────────────────────────────────────────────────────────

class FakeClient:
    """Scripted broker double; records every call."""

    def __init__(self, *, quotes=None, positions=None, orders=None,
                 buying_power=1000.0, total_value=1000.0, portfolio=None, pages=None):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.orders_list = orders or []
        self.pages = pages              # None = one page (the pre-fix behaviour)
        self.buying_power = buying_power
        self.total_value = total_value
        self.portfolio = portfolio          # raw override for payload-shape tests

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        if self.portfolio is not None:
            return self.portfolio
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

    def get_positions(self, acct):
        self.calls.append(("get_positions", acct))
        return {"data": {"positions": [{"symbol": s, "type": "long", "quantity": str(q)}
                                       for s, q in self.positions_list]}}

    def get_quotes(self, symbols):
        self.calls.append(("get_quotes", tuple(symbols)))
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes_map[s])}}
            for s in symbols if s in self.quotes_map]}}

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        self.calls.append(("get_equity_orders", acct, placed_agent, cursor))
        if self.pages is None:
            return {"data": {"orders": list(self.orders_list), "next": None}}
        # scripted pagination: pages[i] is one page. `next` is a BARE opaque
        # token, exactly as Robinhood sends it (cD0yMDI2…, never a URL) — the
        # earlier URL-shaped fake is how a page-1-only truncation stayed green.
        idx = 0 if cursor is None else int(str(cursor).removeprefix("cD0"))
        page = self.pages[idx] if idx < len(self.pages) else []
        nxt = f"cD0{idx + 1}" if idx + 1 < len(self.pages) else None
        return {"data": {"orders": list(page), "next": nxt}}

    def review_market_buy(self, acct, symbol, amt):
        self.calls.append(("review_buy", acct, symbol, amt))
        return {"data": {"order_checks": {}}}

    def review_market_sell(self, acct, symbol, qty):
        self.calls.append(("review_sell", acct, symbol, qty))
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, acct, symbol, amt, ref_id=None):
        self.calls.append(("place_buy", acct, symbol, amt))
        return {"data": {"order": {"id": f"ord-{symbol}", "state": "queued"}}}

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.calls.append(("place_sell", acct, symbol, qty))
        return {"data": {"order": {"id": f"ord-{symbol}-s", "state": "queued"}}}

    def trades_for(self, kind, acct, symbol=None):
        return [c for c in self.calls
                if c[0] == kind and c[1] == acct and (symbol is None or c[2] == symbol)]


def _member(email: str, *, alloc_value: float = 50.0, alloc_type: str = "dollar",
            alloc_applied: float | None = None) -> int:
    """A member who has never deployed (alloc_applied NULL) unless a test says
    otherwise. The earlier default stamped alloc_applied = alloc_value onto a
    member holding nothing — a state that in production means the club has
    lost sight of the deployed dollars, which the mark gate now refuses to
    size against — and it never changed a budget here (an empty slice
    bootstraps from the full stake either way)."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,max_orders,alloc_type,alloc_value,"
            "alloc_applied,updated_at) VALUES(?,1,25,?,?,?,?)",
            (mid, alloc_type, alloc_value, alloc_applied, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?, 'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    _MEMBERS.append(mid)
    return mid


def _closed_session(trade_date: datetime.date, mid: int, picks: list[str]) -> dict:
    object.__setattr__(settings, "top_n", max(settings.top_n, len(picks)))
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], mid, picks)
    return voting.close_and_select(s["id"])


def _use(monkeypatch, client) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)


@contextlib.contextmanager
def _armed():
    """A real armed pass. The guards under test are all gated on will_place, so
    a dry run proves nothing about them — which is exactly how they slipped
    through the first time."""
    prev = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    # The basket floors would downgrade these one- and two-name baskets to
    # review-only, and review-only is exactly the mode that hid these gaps.
    object.__setattr__(settings, "min_basket_size", 1)
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        yield
    finally:
        set_armed(False)
        object.__setattr__(settings, "execution_mode", prev)


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _rows(sid: int, mid: int, symbol: str, side: str = "buy") -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM executions WHERE session_id=? AND member_id=? AND symbol=? AND side=? "
            "ORDER BY id", (sid, mid, symbol, side)).fetchall()]


# ─── A4: supersession happens AFTER the proof, on a live pass ────────────────

def test_live_a4_supersedes_only_after_the_broker_says_absent(monkeypatch):
    """The ordering rule, on the pass where it is not dead code.

    The broker is asked first and answers "no such order"; only then is the
    stale row retired, and only because a replacement is actually going out.
    Doing it the other way round — retiring the row before asking — throws away
    the one record that an order the broker may have ACCEPTED ever existed, and
    then re-places it blind. That is the duplicate buy A4 exists to prevent.
    """
    mid = _member("gap-a4-live@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 4, 1), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 20.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: 400 rejected")
    client = FakeClient(quotes={"AAPL": 100.0}, orders=[], buying_power=100.0,
                        total_value=500.0)
    _use(monkeypatch, client)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    # the broker WAS consulted, and before any replacement went out
    fetches = [i for i, c in enumerate(client.calls) if c[0] == "get_equity_orders"]
    places = [i for i, c in enumerate(client.calls) if c[0] == "place_buy"]
    assert fetches and places and fetches[0] < places[0], client.calls

    rows = _rows(sid, mid, "AAPL")
    assert sorted(r["status"] for r in rows) == ["placed", "superseded"]
    old = next(r for r in rows if r["status"] == "superseded")
    assert old["error"] == "place: 400 rejected", "the evidence is retired, not rewritten"


def test_live_a4_keeps_the_error_row_when_the_broker_order_is_found(monkeypatch):
    """The other branch of the same rule: the order DID land. The row is
    promoted in place — not superseded and re-placed — so the club counts the
    shares it already owns and buys nothing twice."""
    mid = _member("gap-a4-found@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 4, 2), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 20.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: read timeout")
    landed = {"symbol": "AAPL", "side": "buy", "state": "filled", "dollar_amount": "20.00",
              "placed_agent": "agentic", "created_at": int(time.time()),
              "cumulative_quantity": "0.2", "id": "ord-real"}
    client = FakeClient(quotes={"AAPL": 100.0}, orders=[landed], buying_power=100.0,
                        total_value=500.0)
    _use(monkeypatch, client)
    # Isolate A4. The run-start reconcile pass would otherwise promote this row
    # itself (same symbol, side, ET date, seconds apart) and A4 would never see
    # a prior error at all — reconcile's promotion has its own tests; this one
    # is about what A4 does when it finds the order.
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    rows = _rows(sid, mid, "AAPL")
    assert [r["status"] for r in rows] == ["placed"], "promoted IN PLACE — one row, not two"
    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL") == [], "never bought twice"


# ─── PDT: the guard spans sessions, because yesterday's buy is yesterday's ───

def test_sell_leg_pdt_guard_sees_a_buy_from_another_session(monkeypatch):
    """A buy placed this morning under session N is still this morning's buy
    when session N+1 runs an hour later (a next-day ballot opens as soon as the
    close lands, and Execute-now can fire against it). Keying the guard to the
    executing session's own rows would sell shares bought hours ago — the day
    trade the guard exists to stop."""
    mid = _member("gap-pdt-xsession@x.com", alloc_value=50.0)
    earlier = _closed_session(datetime.date(2031, 4, 3), mid, ["TSLA"])
    # bought under the EARLIER session, moments ago (no backdating)
    executor._record(earlier["id"], mid, "TSLA", 25.0,
                     executor.ref_id_for(earlier["id"], mid, "TSLA"), "placed",
                     place={"_concord_est_qty": 0.1})
    closed = _closed_session(datetime.date(2031, 4, 4), mid, ["AAPL"])   # TSLA dropped
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "0.1")], buying_power=100.0, total_value=500.0)
    _use(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, phase="both")

    row = next(r for r in summary["members"] if r["member_id"] == mid)
    sell = next(o for o in row["orders"] if o["symbol"] == "TSLA" and o["side"] == "sell")
    assert sell["status"] == "skipped"
    assert "PDT guard" in sell["note"], sell
    assert client.trades_for("review_sell", f"ACC{mid}", "TSLA") == []


# ─── BUDGET: the strict total_value read, unshadowed ────────────────────────

def test_shape_shifted_payload_never_reads_as_a_tiny_account(monkeypatch):
    """The dangerous half of the strict read, which the club-value cross-check
    cannot cover.

    A member holding little or no club stock has club_value ~ 0, so the
    consistency guard ('an account cannot be worth less than the club positions
    inside it') passes trivially. If total_value could still fall back to
    buying power, a renamed field would read as a real account worth a few
    dollars — a legitimate-looking tiny budget, not an obvious error — and the
    run would size against it. It must fail closed instead.
    """
    mid = _member("gap-tv-strict@x.com", alloc_value=500.0)
    closed = _closed_session(datetime.date(2031, 4, 7), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, buying_power=3.0)
    # total_value renamed by an upstream payload change; nothing is held, so the
    # club-value cross-check has nothing to catch it with
    client.portfolio = {"data": {"buying_power": {"buying_power": "3.0"},
                                 "portfolio_value": "5000.0"}}
    _use(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, phase="both")

    row = next(r for r in summary["members"] if r["member_id"] == mid)
    assert row.get("skip_reason") == "transient_rh_error", row
    assert "account value" in (row.get("note") or "")
    assert client.trades_for("review_buy", f"ACC{mid}") == [], "nothing was sized off $3"


# ─── STRAND: nothing in flight must never hold the flip ─────────────────────

def test_auth_strand_before_any_order_does_not_hold_the_flip(monkeypatch):
    """A token that dies on the member's FIRST review leaves nothing in flight —
    no sold proceeds, no half-done cycle — so the session must still flip to
    'executed'. Holding it open there is how sessions used to wedge: one
    unreachable member froze every voter's ballot for days."""
    from app.robinhood.client import RHAuthError

    mid = _member("gap-strand-nothing@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 4, 8), mid, ["AAPL"])
    sid = closed["id"]

    class DeadToken(FakeClient):
        def review_market_buy(self, acct, symbol, amt):
            raise RHAuthError("MCP HTTP 401: unauthorized")

    client = DeadToken(quotes={"AAPL": 100.0}, buying_power=100.0, total_value=500.0)
    _use(monkeypatch, client)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    row = next(r for r in summary["members"] if r["member_id"] == mid)
    assert row.get("auth_stranded") is not True, "nothing was in flight"
    with db() as conn:
        assert conn.execute("SELECT status FROM vote_sessions WHERE id=?",
                            (sid,)).fetchone()[0] == "executed"
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE ?",
                            (f"strand_hold:{sid}:%",)).fetchone()[0] == 0


# ─── FEED: absence is only proof when the feed provably reaches back ────────

def test_a4_finds_a_landed_order_on_page_two(monkeypatch):
    """The bug this file exists to prevent recurring.

    A4 asks the broker "did my errored buy actually land?" and reads the answer
    from the order feed. It used to read only PAGE ONE. The feed is fetched from
    inside the buy loop — after the sell leg and after this run's earlier buys —
    so the run's own fresh orders crowd page one ahead of the older row being
    verified, and the real fill sits on page two. Reading "not found" there and
    acting on it re-places a buy the broker already executed.
    """
    mid = _member("gap-feed-page2@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 5, 1), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 20.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: read timeout")
    landed = {"symbol": "AAPL", "side": "buy", "state": "filled", "dollar_amount": "20.00",
              "placed_agent": "agentic", "created_at": int(time.time()),
              "cumulative_quantity": "0.2", "id": "ord-real"}
    noise = [{"symbol": "KO", "side": "buy", "state": "filled", "dollar_amount": "1.00",
              "placed_agent": "agentic", "created_at": int(time.time()),
              "cumulative_quantity": "0.01", "id": f"noise-{i}"} for i in range(3)]
    client = FakeClient(quotes={"AAPL": 100.0}, buying_power=100.0, total_value=500.0,
                        pages=[noise, [landed]])          # the match is on page 2
    _use(monkeypatch, client)
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    assert len(client.trades_for("get_equity_orders", f"ACC{mid}")) >= 2, "page 2 was never read"
    rows = _rows(sid, mid, "AAPL")
    assert [r["status"] for r in rows] == ["placed"], rows
    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL") == [], "never bought twice"


def test_unproven_absence_does_not_retire_a_non_basket_row(monkeypatch):
    """'superseded' is terminal in both directions — club_shares counts only
    'placed', and reconcile drops the row from its candidate set — so retiring a
    row on an absence the feed never actually proved makes a REAL fill
    permanently invisible. When the feed stops before reaching the row's own
    timestamp, the row stays 'error' and reconcile's age-out is the backstop."""
    mid = _member("gap-feed-unproven@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 5, 2), mid, ["AAPL"])
    sid = closed["id"]
    # an OLD errored buy of a name that is NOT in today's basket
    executor._record(sid, mid, "ZTS", 9.0, executor.ref_id_for(sid, mid, "ZTS"),
                     "error", error="place: read timeout")
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol='ZTS'",
                     (int(time.time()) - 6 * 86400, mid))
    # a feed that is all RECENT orders and still has more pages: it never reaches
    # back to the row, so it proves nothing either way
    recent = [{"symbol": "KO", "side": "buy", "state": "filled", "dollar_amount": "1.00",
               "placed_agent": "agentic", "created_at": int(time.time()),
               "cumulative_quantity": "0.01", "id": f"r-{i}"} for i in range(4)]
    client = FakeClient(quotes={"AAPL": 100.0, "ZTS": 180.0}, buying_power=100.0,
                        total_value=500.0,
                        pages=[recent] * (executor._A4_MAX_ORDER_PAGES + 2))
    _use(monkeypatch, client)
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    row = _rows(sid, mid, "ZTS")[0]
    assert row["status"] == "error", "an unproven absence must not retire the evidence"
    assert row["error"] == "place: read timeout", "and must not rewrite it"
    # the page cap is respected — it does not read forever
    assert len(client.trades_for("get_equity_orders", f"ACC{mid}")) == executor._A4_MAX_ORDER_PAGES


def test_exhausted_feed_does_retire_a_non_basket_row(monkeypatch):
    """The other half: when the broker hands over the WHOLE feed and the order
    is not in it, absence is real and the row is retired as before."""
    mid = _member("gap-feed-proven@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 5, 3), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "ZTS", 9.0, executor.ref_id_for(sid, mid, "ZTS"),
                     "error", error="place: read timeout")
    client = FakeClient(quotes={"AAPL": 100.0, "ZTS": 180.0}, buying_power=100.0,
                        total_value=500.0, orders=[])      # one page, and it is empty
    _use(monkeypatch, client)
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)

    with _armed():
        executor.run(closed, force_dry_run=False, phase="buy")

    assert _rows(sid, mid, "ZTS")[0]["status"] == "superseded"


# ─── NaN: an unusable quote is a MISSING quote, not a zero ─────────────────

def test_a_nan_quote_is_treated_as_missing_not_as_a_price(monkeypatch):
    """json.loads accepts a bare NaN, and a NaN is truthy AND is not None — so
    it passed the quote filter and then walked straight past the fail-closed
    `if price is None` valuation guard, poisoning club_value. Every later test
    against it reads False (nan < 1.00, nan > 0), which is silently the opposite
    of what the guard was written to do."""
    mid = _member("gap-nan@x.com", alloc_value=50.0)
    executor._record(90840, mid, "MSFT", 25.0, "gap-nan-ref", "placed",
                     place={"_concord_est_qty": 0.25})
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol='MSFT'",
                     (int(time.time()) - 9 * 86400, mid))
    closed = _closed_session(datetime.date(2031, 5, 4), mid, ["AAPL", "MSFT"])

    class NaNQuotes(FakeClient):
        def get_quotes(self, symbols):
            self.calls.append(("get_quotes", tuple(symbols)))
            return {"data": {"results": [
                {"quote": {"symbol": s,
                           "last_trade_price": ("NaN" if s == "MSFT" else str(self.quotes_map[s]))}}
                for s in symbols if s in self.quotes_map or s == "MSFT"]}}

    client = NaNQuotes(quotes={"AAPL": 100.0}, positions=[("MSFT", "0.25")],
                       buying_power=100.0, total_value=500.0)
    _use(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, phase="both")

    row = next(r for r in summary["members"] if r["member_id"] == mid)
    assert row.get("skip_reason") == "unquotable_holding", row
    assert client.trades_for("review_sell", f"ACC{mid}") == [], "nothing sized off a NaN"


def test_unproven_absence_never_re_places_an_in_basket_buy(monkeypatch):
    """The worst outcome a shallow feed read can produce, and the last one closed.

    The in-basket path does not merely retire a row on "not found" — it falls
    through and places a fresh buy. If the feed simply stopped before reaching
    the errored row, that is a SECOND real-money purchase of a name the broker
    may already hold, funded from cash beyond the member's commitment. So an
    unproven absence is treated exactly like an unreadable feed: nothing is
    placed, the error row stands as evidence, and reconcile — which pages the
    same feed and can age the row out — is what eventually returns the dollars.
    Deliberately no carry accrual here: reconcile credits carry_pending when
    absence becomes provable, and accruing now would return the same dollars twice.
    """
    mid = _member("gap-inbasket-unproven@x.com", alloc_value=20.0)
    closed = _closed_session(datetime.date(2031, 5, 5), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 20.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: read timeout")
    with db() as conn:                      # the row is older than anything the feed shows
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol='AAPL'",
                     (int(time.time()) - 6 * 86400, mid))
    recent = [{"symbol": "KO", "side": "buy", "state": "filled", "dollar_amount": "1.00",
               "placed_agent": "agentic", "created_at": int(time.time()),
               "cumulative_quantity": "0.01", "id": f"r-{i}"} for i in range(4)]
    client = FakeClient(quotes={"AAPL": 100.0}, buying_power=100.0, total_value=500.0,
                        pages=[recent] * (executor._A4_MAX_ORDER_PAGES + 2))
    _use(monkeypatch, client)
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)

    with _armed():
        summary = executor.run(closed, force_dry_run=False, phase="buy")

    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL") == [], "never bought twice"
    row = _rows(sid, mid, "AAPL")[0]
    assert row["status"] == "error", "the evidence stands until someone can prove otherwise"
    assert row["error"] == "place: read timeout"
    order = next(o for o in _mrow(summary, mid)["orders"] if o["symbol"] == "AAPL")
    assert order["status"] == "skipped" and "unproven" in order["note"], order
