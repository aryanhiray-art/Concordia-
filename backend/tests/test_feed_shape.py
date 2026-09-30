"""The broker's order feed, as it really is — and what a partial read must do.

Robinhood pages its order feed with `next` as a BARE opaque token
(`cD0yMDI2LTA5LTEx…`, a base64 `p=<timestamp>`), never a URL. Three private
copies of a cursor parser assumed `?cursor=` in a URL, returned None on the
token, and every feed read silently ended at page one (200 orders). The
derived club position under-counted, the sizing "ride" anchored the members'
commitments to that mark, and the write-back persisted it: a $990 commitment
became $82 in four cycles with a green test suite — because the test fake
emitted a URL-shaped `next`.

Pinned here, in the order they failed:
  1. the cursor decoder accepts the real shape (and the URL shape, and refuses
     to confuse "no next page" with "a next page I cannot read");
  2. the full-feed reader pages to the end, and fails CLOSED on a `next` it
     cannot decode or a feed that stops short of the club's own ledger;
  3. the executor's A4 reader never claims exhaustion it cannot prove;
  4. the mark plausibility gate: a slice that reads far below what was
     deployed while the account is intact is a slice the club lost SIGHT of,
     and the run skips the member instead of riding the commitment down.

Run from backend/:  python -m pytest tests/test_feed_shape.py -q
(throwaway temp DB; the Robinhood client + vault are mocked — no network.)
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

from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_get, kv_set  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.robinhood.feed import cursor_from_next, normalize_orders  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, positions, reconcile, voting  # noqa: E402

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
            conn.execute("DELETE FROM kv WHERE key=?", (f"mark_accept:{mid}",))
        for sid in _SESSIONS:
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))
    positions.invalidate_feed_cache()


# ─── broker double: pages exactly like Robinhood ─────────────────────────────

class _Feed:
    """`pages[i]` is one page. `next` is a bare token by default — the REAL
    shape — or a URL / an undecodable dict on request. Last page: next=None."""

    def __init__(self, pages, *, next_shape="token", quotes=None, positions=None,
                 buying_power=5.0, total_value=110.0, pos_pages=None, pos_next_shape="token"):
        self.pages = pages
        self.next_shape = next_shape
        self.pos_pages = pos_pages              # scripted positions pagination (same rules)
        self.pos_next_shape = pos_next_shape
        self.quotes = quotes or {}
        self.positions_list = positions or []
        self.buying_power = buying_power
        self.total_value = total_value
        self.calls: list[tuple] = []

    def get_equity_orders(self, acct, cursor=None, placed_agent=None):
        self.calls.append(("get_equity_orders", acct, cursor, placed_agent))
        idx = 0 if cursor is None else int(str(cursor).removeprefix("cD0"))
        page = self.pages[idx] if idx < len(self.pages) else []
        if idx + 1 >= len(self.pages):
            nxt = None
        elif self.next_shape == "token":
            nxt = f"cD0{idx + 1}"
        elif self.next_shape == "url":
            nxt = f"https://api.robinhood.com/orders/?cursor=cD0{idx + 1}"
        else:
            nxt = {"page": idx + 1}          # a shape nobody decodes
        return {"data": {"orders": list(page), "next": nxt}}

    def get_portfolio(self, acct):
        return {"data": {"buying_power": {"buying_power": str(self.buying_power)},
                         "total_value": str(self.total_value)}}

    def get_positions(self, acct, cursor=None):
        self.calls.append(("get_positions", acct, cursor))
        if self.pos_pages is None:
            return {"data": {"positions": [{"symbol": s, "type": "long", "quantity": str(q)}
                                           for s, q in self.positions_list]}}
        idx = 0 if cursor is None else int(str(cursor).removeprefix("cD0"))
        page = self.pos_pages[idx] if idx < len(self.pos_pages) else []
        if idx + 1 >= len(self.pos_pages):
            nxt = None
        elif self.pos_next_shape == "token":
            nxt = f"cD0{idx + 1}"
        else:
            nxt = {"page": idx + 1}
        return {"data": {"next": nxt, "positions": [
            {"symbol": s, "type": "long", "quantity": str(q)} for s, q in page]}}

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes[s])}}
            for s in symbols if s in self.quotes]}}

    def review_market_buy(self, acct, symbol, amt):
        return {"data": {"order_checks": {}}}

    def review_market_sell(self, acct, symbol, qty):
        return {"data": {"order_checks": {}}}

    def place_market_buy(self, *a, **k):
        raise AssertionError("a dry run must never place")

    def place_market_sell(self, *a, **k):
        raise AssertionError("a dry run must never place")

    def fetches(self):
        return [c for c in self.calls if c[0] == "get_equity_orders"]


def _order(sym: str, side: str, qty: float, days_ago: float, agent: str = "agentic") -> dict:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    return {"id": f"{sym}-{side}-{days_ago}", "symbol": sym, "side": side, "state": "filled",
            "quantity": f"{qty:.6f}", "cumulative_quantity": f"{qty:.6f}",
            "average_price": "100.00", "placed_agent": agent,
            "created_at": ts.isoformat().replace("+00:00", "Z")}


def _member(email: str, *, alloc_value: float = 100.0) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,max_orders,alloc_type,alloc_value,"
            "alloc_applied,compound,updated_at) VALUES(?,1,25,'dollar',?,?,1,?)",
            (mid, alloc_value, alloc_value, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?, 'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    _MEMBERS.append(mid)
    return mid


def _ledger_buy(mid: int, sym: str, qty: float, days_ago: float) -> None:
    """A placed club buy in the ledger, `days_ago` old — the club's own history."""
    executor._record(90900, mid, sym, qty * 100.0, f"feed-shape-{mid}-{sym}-{days_ago}",
                     "placed", place={"_concord_est_qty": qty})
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol=? "
                     "AND session_id=90900",
                     (int(time.time() - days_ago * 86400), mid, sym))


def _derived(mid: int, client: _Feed) -> dict[str, float]:
    positions.invalidate_feed_cache()
    return positions.club_shares_derived(mid, client=client, account_number=f"ACC{mid}")


# ─── 1. the cursor decoder ───────────────────────────────────────────────────

@pytest.mark.parametrize("nxt,want", [
    ("cD0yMDI2LTA5LTExKzE0JTNBMTclM0E1OC4zMzYwNDclMkIwMCUzQTAw",
     "cD0yMDI2LTA5LTExKzE0JTNBMTclM0E1OC4zMzYwNDclMkIwMCUzQTAw"),   # the real thing
    ("  cD0abc  ", "cD0abc"),                                        # padded token
    ("https://api.robinhood.com/orders/?cursor=cD0abc&x=1", "cD0abc"),  # URL form
    ("/orders/?cursor=cD0abc", "cD0abc"),                            # relative URL
    ({"cursor": "cD0abc"}, "cD0abc"),                                # wrapped
    (None, None), ("", None), ("   ", None),                         # no next page
    ("https://api.robinhood.com/orders/?page=2", None),              # URL, no cursor
    ({"page": 2}, None),                                             # undecodable
])
def test_cursor_from_next_decodes_every_shape_the_broker_has_used(nxt, want):
    assert cursor_from_next(nxt) == want


def test_normalize_orders_hands_back_the_raw_next_so_callers_can_tell_the_cases_apart():
    orders, nxt = normalize_orders({"data": {"orders": [{"id": 1}, "junk"], "next": {"page": 2}}})
    assert orders == [{"id": 1}]
    assert nxt == {"page": 2}, "undecodable is not the same as absent"
    assert normalize_orders([{"id": 1}]) == ([{"id": 1}], None)
    assert normalize_orders({"data": {"results": [], "next": None}}) == ([], None)


# ─── 2. the full-feed reader ─────────────────────────────────────────────────

def test_full_feed_pages_through_bare_tokens_to_the_end():
    """Three pages, real-shaped `next`. Revert the decoder and page one is all
    the club ever sees: the 45-day-old AAPL buy vanishes and the read fails
    the reach check instead of returning a position."""
    mid = _member("feed-pages@x.com")
    _ledger_buy(mid, "AAPL", 0.2, days_ago=45)
    _ledger_buy(mid, "MSFT", 0.2, days_ago=20)
    client = _Feed([[_order("AAPL", "buy", 0.1, 1)],
                    [_order("MSFT", "buy", 0.2, 20)],
                    [_order("AAPL", "buy", 0.1, 45)]])

    got = _derived(mid, client)

    assert got == {"AAPL": pytest.approx(0.2), "MSFT": pytest.approx(0.2)}
    assert [c[2] for c in client.fetches()] == [None, "cD01", "cD02"], client.calls


def test_full_feed_still_pages_a_url_shaped_next():
    mid = _member("feed-url@x.com")
    _ledger_buy(mid, "AAPL", 0.2, days_ago=45)
    client = _Feed([[_order("AAPL", "buy", 0.1, 1)], [_order("AAPL", "buy", 0.1, 45)]],
                   next_shape="url")
    assert _derived(mid, client) == {"AAPL": pytest.approx(0.2)}
    assert len(client.fetches()) == 2


def test_an_undecodable_next_fails_closed_and_caches_nothing():
    """A next page exists that we cannot fetch. Stopping quietly at page one
    is the exact truncation this module was rebuilt to prevent."""
    mid = _member("feed-undecodable@x.com")
    _ledger_buy(mid, "AAPL", 0.2, days_ago=45)
    client = _Feed([[_order("AAPL", "buy", 0.1, 1)], [_order("AAPL", "buy", 0.1, 45)]],
                   next_shape="opaque-dict")
    with pytest.raises(positions.DerivationIncomplete, match="undecodable"):
        _derived(mid, client)
    assert f"ACC{mid}" not in positions._FEED_CACHE, "a failed read must not be served later"


def test_a_feed_that_stops_short_of_the_ledger_is_partial_not_complete():
    """The broker says next=None after one page, but the club's own ledger has
    a placed buy 40 days older than anything in it. That is a feed with a hole
    in it — the 2026-09-24 collapse — and the read must say so, on the record."""
    mid = _member("feed-short@x.com")
    _ledger_buy(mid, "AAPL", 0.2, days_ago=40)
    client = _Feed([[_order("AAPL", "buy", 0.05, 1), _order("MSFT", "buy", 0.1, 2)]])
    with pytest.raises(positions.DerivationIncomplete, match="does not reach"):
        _derived(mid, client)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='derived_feed_short' "
                         "AND detail_json LIKE '%\"orders\": 2%'").fetchone()[0]
    assert n >= 1, "a short feed leaves an audit row, not a silent under-count"


def test_an_empty_feed_against_a_ledger_with_history_is_incomplete_not_flat():
    """Empty is what a wrong account, a renamed envelope key or an auth hiccup
    looks like. With club history behind it, empty means 'could not read',
    never 'holds nothing' — the latter would re-buy the whole basket."""
    mid = _member("feed-empty@x.com")
    _ledger_buy(mid, "AAPL", 0.2, days_ago=10)
    with pytest.raises(positions.DerivationIncomplete):
        _derived(mid, _Feed([[]]))


def test_a_member_with_no_club_history_derives_from_whatever_the_feed_holds():
    """No ledger rows → nothing to reach → the feed is taken at its word (and a
    never-traded symbol is still dropped by the ledger gate)."""
    mid = _member("feed-fresh@x.com")
    assert _derived(mid, _Feed([[_order("TSLA", "buy", 1.0, 1)]])) == {}


def test_the_feed_cache_is_rechecked_against_the_ledger():
    """A feed cached moments ago for a member whose ledger reaches further back
    than it does is still partial — the cache must not launder it."""
    mid = _member("feed-cache@x.com")
    client = _Feed([[_order("AAPL", "buy", 0.1, 1)]])
    assert _derived(mid, client) == {}                     # no history yet: cached
    _ledger_buy(mid, "AAPL", 0.2, days_ago=40)             # history appears
    with pytest.raises(positions.DerivationIncomplete, match="does not reach"):
        positions.club_shares_derived(mid, client=client, account_number=f"ACC{mid}")


# ─── 3. the executor's A4 reader and reconcile's ─────────────────────────────

def test_a4_reader_pages_bare_tokens_and_claims_exhaustion_only_when_proven():
    pages = [[_order("AAPL", "buy", 0.1, 1)], [_order("MSFT", "buy", 0.1, 2)],
             [_order("AMZN", "buy", 0.1, 3)]]
    orders, exhausted = executor._fetch_recent_orders(_Feed(pages), "ACC")
    assert [o["symbol"] for o in orders] == ["AAPL", "MSFT", "AMZN"]
    assert exhausted is True

    orders, exhausted = executor._fetch_recent_orders(_Feed(pages, next_shape="opaque-dict"), "ACC")
    assert [o["symbol"] for o in orders] == ["AAPL"]
    assert exhausted is False, "a page we could not fetch is not proof of absence"


def test_reconcile_reader_pages_bare_tokens():
    pages = [[_order("AAPL", "buy", 0.1, 1)], [_order("MSFT", "sell", 0.1, 2, agent="user")],
             [_order("AMZN", "buy", 0.1, 3)]]
    got = reconcile._fetch_agentic_orders(_Feed(pages), "ACC")
    assert [o["symbol"] for o in got] == ["AAPL", "AMZN"], "three pages read, personal row dropped"


# ─── 4. the mark plausibility gate ───────────────────────────────────────────

def _closed_session(trade_date: datetime.date, mid: int, picks: list[str]) -> dict:
    object.__setattr__(settings, "top_n", max(settings.top_n, len(picks)))
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], mid, picks)
    return voting.close_and_select(s["id"])


def _run(monkeypatch, mid: int, closed: dict, client: _Feed) -> dict:
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    # reconcile has its own tests; keep its run-start pass out of this one
    monkeypatch.setattr(executor.reconcile, "reconcile_all", lambda dry_run=False: None)
    summary = executor.run(closed, force_dry_run=True, phase="both")
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _alloc(mid: int) -> tuple[float, float]:
    with db() as conn:
        r = conn.execute("SELECT alloc_value, alloc_applied FROM member_settings "
                         "WHERE member_id=?", (mid,)).fetchone()
    return r["alloc_value"], r["alloc_applied"]


def test_a_collapsed_mark_in_an_intact_account_skips_the_member(monkeypatch):
    """$100 deployed last cycle; the club can now see $20 of it while the
    account is still worth $110. The ride would have set the commitment to
    $20 and the write-back would have kept it. Skip, audit, leave the stake."""
    mid = _member("mark-suspect@x.com", alloc_value=100.0)
    _ledger_buy(mid, "AAPL", 0.2, days_ago=3)
    closed = _closed_session(datetime.date(2032, 1, 5), mid, ["AAPL"])
    client = _Feed([[]], quotes={"AAPL": 100.0}, positions=[("AAPL", "0.2")],
                    buying_power=5.0, total_value=110.0)

    row = _run(monkeypatch, mid, closed, client)

    assert row.get("skip_reason") == "slice_mark_suspect", row
    assert "mark_accept" in str(row.get("note")), "the note tells the admin how to honor it"
    assert _alloc(mid) == (100.0, 100.0), "the commitment is untouched"
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='slice_mark_suspect' "
                         "AND detail_json LIKE ?", (f'%"member_id": {mid},%',)).fetchone()[0]
    assert n == 1


def test_a_real_loss_is_honored_the_account_fell_with_the_slice(monkeypatch):
    """Same $20 slice, but the account is worth $21: the money is gone, not
    hidden. The ride proceeds and the member is sized normally."""
    mid = _member("mark-loss@x.com", alloc_value=100.0)
    _ledger_buy(mid, "AAPL", 0.2, days_ago=3)
    closed = _closed_session(datetime.date(2032, 1, 6), mid, ["AAPL"])
    client = _Feed([[]], quotes={"AAPL": 100.0}, positions=[("AAPL", "0.2")],
                    buying_power=1.0, total_value=21.0)

    row = _run(monkeypatch, mid, closed, client)

    assert row.get("skip_reason") is None, row
    assert row.get("budget") is not None or row.get("orders") is not None


def test_an_admin_can_accept_the_smaller_slice_once(monkeypatch):
    """The member sold club shares by hand and the admin confirmed it: the
    override honors the mark. A dry run leaves the token in place — only the
    live pass that acts on it consumes it."""
    mid = _member("mark-accept@x.com", alloc_value=100.0)
    _ledger_buy(mid, "AAPL", 0.2, days_ago=3)
    closed = _closed_session(datetime.date(2032, 1, 7), mid, ["AAPL"])
    client = _Feed([[]], quotes={"AAPL": 100.0}, positions=[("AAPL", "0.2")],
                    buying_power=5.0, total_value=110.0)
    kv_set(f"mark_accept:{mid}", "1")

    row = _run(monkeypatch, mid, closed, client)

    assert row.get("skip_reason") is None, row
    assert kv_get(f"mark_accept:{mid}") == "1", "a dry run must not spend the override"
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='slice_mark_accepted' "
                         "AND detail_json LIKE ?", (f'%"member_id": {mid},%',)).fetchone()[0]
    assert n == 1


def test_a_small_drift_never_trips_the_gate(monkeypatch):
    """$100 deployed, $90 visible, account $110: ordinary market noise plus a
    little idle cash — well inside the tolerance, sized as usual."""
    mid = _member("mark-drift@x.com", alloc_value=100.0)
    _ledger_buy(mid, "AAPL", 0.9, days_ago=3)
    closed = _closed_session(datetime.date(2032, 1, 8), mid, ["AAPL"])
    client = _Feed([[]], quotes={"AAPL": 100.0}, positions=[("AAPL", "0.9")],
                    buying_power=10.0, total_value=110.0)
    row = _run(monkeypatch, mid, closed, client)
    assert row.get("skip_reason") is None, row


# ─── 5. the sibling readers: positions pages, order amounts ──────────────────

def test_order_amount_reads_the_broker_s_real_key():
    """Robinhood puts a dollar order's notional under dollar_based_amount.amount
    and nowhere else. Both service-level helpers looked for five other keys, so
    A4 could never match an errored buy that landed and reconcile could never
    promote one — since launch, silently."""
    from app.robinhood.feed import order_amount
    real = {"id": "x", "side": "buy", "dollar_based_amount": {"amount": "1.180000", "currency_code": "USD"},
            "quantity": "0.005685", "cumulative_quantity": "0.005685", "average_price": "207.5431"}
    assert order_amount(real) == pytest.approx(1.18)
    assert order_amount({"dollar_amount": "20.00"}) == pytest.approx(20.0)        # legacy shape
    assert order_amount({"side": "sell", "quantity": "0.5"}) is None               # share-denominated
    assert order_amount({"dollar_based_amount": {"amount": "NaN"}}) is None       # never NaN
    assert executor._order_amount(real) == pytest.approx(1.18)
    assert reconcile._order_amount(real) == pytest.approx(1.18)


def test_positions_reader_follows_next_to_the_end(monkeypatch):
    """get_equity_positions pages like the order feed. A reader that stops at
    page one reports everything on page two as 'held nothing' — the sell leg
    leaves it alone and the buy leg re-buys it."""
    mid = _member("pos-pages@x.com")
    client = _Feed([[]], pos_pages=[[("AAPL", "0.2")], [("MSFT", "0.3")]])
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    got = {p["symbol"]: p["quantity"] for p in positions.live_positions_strict(mid)}
    assert got == {"AAPL": pytest.approx(0.2), "MSFT": pytest.approx(0.3)}
    assert [c[2] for c in client.calls if c[0] == "get_positions"] == [None, "cD01"]


def test_positions_reader_fails_closed_on_an_undecodable_next(monkeypatch):
    """A second page we cannot fetch: the strict reader raises (its callers skip
    the member), the lenient display reader returns [] as its contract says."""
    mid = _member("pos-undecodable@x.com")
    client = _Feed([[]], pos_pages=[[("AAPL", "0.2")], [("MSFT", "0.3")]], pos_next_shape="opaque")
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    with pytest.raises(RuntimeError, match="undecodable"):
        positions.live_positions_strict(mid)
    assert positions.holdings_with_value(mid) == []
