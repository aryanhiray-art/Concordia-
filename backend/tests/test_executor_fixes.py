"""Tests for the audited executor fixes (A4/A5/A7/A8).

A4 — duplicate-buy window: an errored live buy is verified against the broker's
     own recent orders before EVER being re-placed (recover or skip, never blind).
A5 — transient vs permanent Robinhood errors: only auth failures strand a member
     (rh_status='error'); anything else retries once then skips this run only.
A7 — full-position exits sweep dust-sized fill residue; personal positions are
     never swept. Plus the standalone dust_cleanup pipeline.
A8 — every skipped member carries a stable skip_reason (incl. opted-out rows).

Run from backend/:  python -m pytest tests/ -q
(uses a throwaway temp DB; the Robinhood client + vault are mocked — no network).
"""
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

from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.robinhood.client import RHAuthError, RHTransientError  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402


def setup_module(_):
    init_db()
    seed_universe()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The A5 retry sleeps 2s between attempts — skip that in tests."""
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, opted_in: int = 1, alloc_value: float = 50.0,
            connect: bool = True, rh_status: str = "connected") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
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
                (mid, f"ACC{mid}", 1, b"enc-tokens", int(time.time()), int(time.time()), rh_status),
            )
    return mid


def _closed_session(trade_date: datetime.date, voter_mid: int, picks: list[str]) -> dict:
    s = voting.open_session_for(trade_date)
    voting.set_member_picks(s["id"], voter_mid, picks)
    return voting.close_and_select(s["id"])


def _mrow(summary: dict, mid: int) -> dict:
    return next(r for r in summary["members"] if r["member_id"] == mid)


def _backdate(sid: int, mid: int, days: int = 5) -> None:
    """Make a seeded prior-cycle row look like a prior cycle.

    `executor._record` stamps created_at=now, so a holding seeded to represent
    "what the club already owns" reads to the sell leg's time-based PDT guard as
    bought THIS morning — every exit/trim/resize is then skipped with "bought
    today — PDT guard". 5 days clears the guard's cutoff (the previous trading
    day's 16:00 ET) no matter which weekday the suite runs on.
    """
    with db() as conn:
        conn.execute(
            "UPDATE executions SET created_at=? WHERE session_id=? AND member_id=?",
            (int(time.time()) - days * 86400, sid, mid),
        )


def _exec_rows(sid: int, mid: int, symbol: str, side: str = "buy") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM executions WHERE session_id=? AND member_id=? AND symbol=? AND side=?",
            (sid, mid, symbol, side),
        ).fetchall()
    return [dict(r) for r in rows]


def _rh_status(mid: int) -> str:
    with db() as conn:
        return conn.execute(
            "SELECT status FROM rh_accounts WHERE member_id=?", (mid,)
        ).fetchone()["status"]


def _skip_audits() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE action='member_skipped'"
        ).fetchall()
    return [json.loads(r["detail_json"]) for r in rows]


class FakeClient:
    """Scripted RobinhoodClient double; records every call it receives."""

    def __init__(self, *, quotes=None, positions=None, orders=None, orders_exc=None,
                 portfolio_exc=None, buying_power=1000.0, total_value=1000.0):
        self.calls: list[tuple] = []
        self.quotes_map = quotes or {}
        self.positions_list = positions or []
        self.orders_list = orders or []
        self.orders_exc = orders_exc
        self.portfolio_exc = portfolio_exc
        self.buying_power = buying_power
        self.total_value = total_value

    def get_portfolio(self, acct):
        self.calls.append(("get_portfolio", acct))
        if self.portfolio_exc is not None:
            raise self.portfolio_exc
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


def _use_client(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(vault, "ensure_client", lambda mid: client)


# ─── A4: duplicate-buy window ────────────────────────────────────────────────

def test_recovers_prior_errored_buy_from_broker(monkeypatch):
    """A prior 'error' buy that the broker actually accepted is recovered as
    'placed' (with _concord_recovered) instead of being re-placed/re-reviewed."""
    mid = _member("a4-recover@x.com")
    closed = _closed_session(datetime.date(2026, 12, 1), mid, ["AAPL"])
    sid = closed["id"]
    # a prior run recorded the buy as errored AFTER the broker may have accepted it
    executor._record(sid, mid, "AAPL", 500.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: connection reset")
    # broker shows a matching order (no ref echo → symbol+side+time+amount match)
    iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    client = FakeClient(
        quotes={"AAPL": 200.0},
        orders=[{"symbol": "AAPL", "side": "buy", "created_at": iso_now,
                 "dollar_based_amount": {"amount": "500.00", "currency_code": "USD"}, "state": "filled"}],
    )
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    rows = _exec_rows(sid, mid, "AAPL", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "placed"
    place = json.loads(rows[0]["place_json"])
    assert place["_concord_recovered"] is True
    # est qty stamped so club_shares reconstruction keeps working (500 / 200)
    assert place["_concord_est_qty"] == pytest.approx(2.5)
    # never re-placed and never even re-reviewed
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert not client.trades_for("review_buy", f"ACC{mid}", "AAPL")
    order = next(o for o in _mrow(summary, mid)["orders"] if o["symbol"] == "AAPL")
    assert order["status"] == "placed" and "recovered" in order["note"]


def test_recovery_matches_on_ref_id_echo(monkeypatch):
    """When the MCP echoes our deterministic ref_id, that alone matches."""
    mid = _member("a4-refid@x.com")
    closed = _closed_session(datetime.date(2026, 12, 2), mid, ["AAPL"])
    sid = closed["id"]
    ref_id = executor.ref_id_for(sid, mid, "AAPL")
    executor._record(sid, mid, "AAPL", 500.0, ref_id, "error", error="place: boom")
    # order carries our ref_id but a stale timestamp + different amount fields
    client = FakeClient(quotes={"AAPL": 200.0},
                        orders=[{"symbol": "AAPL", "side": "buy", "ref_id": ref_id}])
    _use_client(monkeypatch, client)

    executor.run(closed, force_dry_run=True)

    rows = _exec_rows(sid, mid, "AAPL", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "placed"
    assert json.loads(rows[0]["place_json"])["_concord_recovered"] is True
    assert not client.trades_for("place_buy", f"ACC{mid}")


def test_unverifiable_prior_error_is_skipped_not_replaced(monkeypatch):
    """If the broker orders can't be fetched, NEVER re-place blind — skip.

    Contract change (error-evidence lifecycle): the unverifiable case no longer
    overwrites the row with a 'skipped' shadow. The 'error' row is the only proof
    that an order the broker may have accepted exists, so it survives UNTOUCHED —
    original status, original error text, original created_at (which is what keeps
    the next run's match window honest). The skip is reported on the run summary,
    not by mutating the evidence.
    """
    mid = _member("a4-unverif@x.com")
    closed = _closed_session(datetime.date(2026, 12, 3), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 500.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: timeout")
    before = _exec_rows(sid, mid, "AAPL", "buy")[0]
    client = FakeClient(quotes={"AAPL": 200.0}, orders_exc=RuntimeError("MCP HTTP 500"))
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    rows = _exec_rows(sid, mid, "AAPL", "buy")
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert rows[0]["error"] == "place: timeout"
    assert rows[0]["created_at"] == before["created_at"]
    assert not client.trades_for("place_buy", f"ACC{mid}")
    assert not client.trades_for("review_buy", f"ACC{mid}", "AAPL")
    order = next(o for o in _mrow(summary, mid)["orders"] if o["symbol"] == "AAPL")
    assert order["status"] == "skipped"
    assert order["note"] == "unverifiable prior error — manual check"


def test_verified_absent_prior_error_replaces_normally(monkeypatch):
    """Broker orders fetched fine but show NO matching order → the prior place
    never landed, so the normal (re-)placement path proceeds.

    Contract change (error-evidence lifecycle): a re-placed name legitimately has
    TWO rows. The old 'error' row is retired to terminal 'superseded' ONLY at the
    moment a new order actually goes to the broker — this run is a dry run, which
    places nothing, so the evidence stays 'error' and the re-plan is recorded
    beside it as 'reviewed'. What must never appear is a 'placed' row.
    """
    mid = _member("a4-absent@x.com")
    closed = _closed_session(datetime.date(2026, 12, 4), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 500.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: 400 rejected")
    client = FakeClient(quotes={"AAPL": 200.0}, orders=[])   # broker has nothing
    _use_client(monkeypatch, client)

    executor.run(closed, force_dry_run=True)

    rows = _exec_rows(sid, mid, "AAPL", "buy")
    assert sorted(r["status"] for r in rows) == ["error", "reviewed"]
    assert next(r for r in rows if r["status"] == "error")["error"] == "place: 400 rejected"
    assert client.trades_for("review_buy", f"ACC{mid}", "AAPL")


def test_a4_ignores_personal_order(monkeypatch):
    """A prior errored club buy + a PERSONAL ('user') broker order of the same
    name/amount is NOT matched by A4 (the fetch is agentic-scoped) → no phantom club
    'placed' row; the club re-reviews its own buy instead of adopting personal shares.

    As in the verified-absent case, the re-plan sits BESIDE the surviving 'error'
    row (a dry run places nothing, so nothing supersedes it) — the assertion that
    matters is that no row anywhere reads 'placed'.
    """
    mid = _member("a4-personal@x.com")
    closed = _closed_session(datetime.date(2026, 12, 11), mid, ["AAPL"])
    sid = closed["id"]
    executor._record(sid, mid, "AAPL", 500.0, executor.ref_id_for(sid, mid, "AAPL"),
                     "error", error="place: boom")
    personal = {"symbol": "AAPL", "side": "buy", "state": "filled", "dollar_amount": 500.0,
                "placed_agent": "user", "created_at": int(time.time())}   # member's OWN order
    client = FakeClient(quotes={"AAPL": 200.0}, orders=[personal])
    _use_client(monkeypatch, client)

    executor.run(closed, force_dry_run=True)

    rows = _exec_rows(sid, mid, "AAPL", "buy")
    assert sorted(r["status"] for r in rows) == ["error", "reviewed"]   # re-reviewed
    assert all(r["status"] != "placed" for r in rows)            # personal order never adopted


# ─── A5: transient vs permanent Robinhood errors ─────────────────────────────

def test_transient_portfolio_error_skips_without_stranding(monkeypatch):
    """A 503-style failure retries once, then skips THIS RUN; rh_status stays
    'connected' so the next run tries again."""
    mid = _member("a5-transient@x.com")
    closed = _closed_session(datetime.date(2026, 12, 5), mid, ["AAPL"])
    client = FakeClient(portfolio_exc=RHTransientError("MCP HTTP 503: upstream"))
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "transient_rh_error"
    assert row["note"] == "transient portfolio error — will retry next run"
    assert _rh_status(mid) == "connected"
    # one retry attempted → two portfolio fetches for this member's account
    assert len([c for c in client.calls if c == ("get_portfolio", f"ACC{mid}")]) == 2
    assert {"member_id": mid, "reason": "transient_rh_error"} in _skip_audits()


def test_auth_error_strands_member(monkeypatch):
    """A 401 marks rh_status='error' (needs re-OAuth / admin rh-reset), no retry."""
    mid = _member("a5-auth@x.com")
    closed = _closed_session(datetime.date(2026, 12, 6), mid, ["AAPL"])
    client = FakeClient(portfolio_exc=RHAuthError("MCP HTTP 401: unauthorized"))
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    row = _mrow(summary, mid)
    assert row["skip_reason"] == "rh_error_stranded"
    assert _rh_status(mid) == "error"
    assert len([c for c in client.calls if c == ("get_portfolio", f"ACC{mid}")]) == 1


def test_vault_reset_status_unstrands():
    """Admin rh-reset flips 'error' back to 'connected' only when tokens remain."""
    mid = _member("a5-reset@x.com", opted_in=0, rh_status="error")
    res = vault.reset_status(mid)
    assert res["ok"] is True and res["previous_status"] == "error"
    assert _rh_status(mid) == "connected"
    # without stored tokens the reset refuses (member must re-OAuth)
    with db() as conn:
        conn.execute("UPDATE rh_accounts SET enc_tokens=NULL, status='error' WHERE member_id=?", (mid,))
    res2 = vault.reset_status(mid)
    assert res2["ok"] is False
    assert _rh_status(mid) == "error"
    with pytest.raises(LookupError):
        vault.reset_status(999999)


# ─── A7: full-position exits sweep dust ──────────────────────────────────────

def test_exit_sell_sweeps_dust_residual(monkeypatch):
    """Full exit with live=1.0005 vs club estimate=1.0 sweeps the crumb: it sells the
    full live quantity (the extra 0.0005 is club residue, not a personal holding),
    floored to Robinhood's 6dp fractional limit."""
    mid = _member("a7-dust@x.com")
    # club history: a placed buy estimated at 1.0 TSLA share (old session)
    executor._record(90001, mid, "TSLA", 250.0, "old-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90001, mid)
    closed = _closed_session(datetime.date(2026, 12, 7), mid, ["AAPL"])  # TSLA dropped
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "1.0005")])
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    sells = client.trades_for("review_sell", f"ACC{mid}", "TSLA")
    assert len(sells) == 1
    assert sells[0][3] == "1.000500"          # full live, swept, 6dp (RH fractional max)
    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "TSLA" and o["side"] == "sell")
    assert "dust" in order["note"]
    rows = _exec_rows(closed["id"], mid, "TSLA", "sell")
    assert len(rows) == 1 and rows[0]["status"] == "reviewed"


def test_exit_sell_never_sweeps_personal_position(monkeypatch):
    """Live residual worth $500 (a personal holding) is NOT dust — only the club
    estimate is sold."""
    mid = _member("a7-personal@x.com")
    executor._record(90002, mid, "TSLA", 250.0, "old-ref-2", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90002, mid)
    closed = _closed_session(datetime.date(2026, 12, 8), mid, ["AAPL"])
    # member personally holds 2 extra TSLA shares (~$500 at $250)
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "3.0")])
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    sells = client.trades_for("review_sell", f"ACC{mid}", "TSLA")
    assert len(sells) == 1
    assert sells[0][3] == "1.000000"                     # est only (1.0), 6dp; personal 2.0 untouched
    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "TSLA" and o["side"] == "sell")
    assert order["note"] == "exited basket"


def test_trim_reconciles_inflated_ledger_to_live(monkeypatch):
    """Sell sizing reads the LIVE account, not the drifted club ledger. When the
    ledger over-estimates a still-in-basket holding (est 10 sh vs 0.02 sh really
    held), the reconciled trim sizes off min(ledger, live) and sees the real 0.02
    is far UNDER target — so it is NOT drained to dust. (Old ledger-sized code
    computed excess=10-target and market-sold the entire 0.02 live position.)"""
    mid = _member("recon-trim@x.com")
    # ledger THINKS the club holds 10 GOOGL; the account really holds 0.02
    executor._record(90010, mid, "GOOGL", 999.0, "old-ref-recon", "placed",
                     place={"_concord_est_qty": 10.0})
    _backdate(90010, mid)
    closed = _closed_session(datetime.date(2026, 12, 12), mid, ["GOOGL"])  # stays in basket
    client = FakeClient(quotes={"GOOGL": 100.0}, positions=[("GOOGL", "0.02")])
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    # reconciled sizing: min(10, 0.02)=0.02 << target → no sell at all (kept/underweight)
    assert client.trades_for("review_sell", f"ACC{mid}", "GOOGL") == []
    assert not client.trades_for("place_sell", f"ACC{mid}", "GOOGL")
    sell_row = next((o for o in _mrow(summary, mid)["orders"]
                     if o["symbol"] == "GOOGL" and o.get("side") == "sell"), None)
    assert sell_row is None


def test_dust_cleanup_dry_run_previews(monkeypatch):
    """dust_cleanup finds sub-$1 sub-0.01-share crumbs on club-traded names only,
    reviews them, and (dry_run) records/places nothing."""
    mid = _member("a7-cleanup@x.com", opted_in=0)
    # club traded NVDA (crumb) and MSFT (real position, too big to be dust)
    executor._record(90003, mid, "NVDA", 100.0, "old-ref-3", "placed",
                     place={"_concord_est_qty": 0.9992})
    executor._record(90003, mid, "MSFT", 500.0, "old-ref-4", "placed",
                     place={"_concord_est_qty": 5.0})
    _backdate(90003, mid)
    client = FakeClient(
        quotes={"NVDA": 100.0, "MSFT": 100.0, "GME": 20.0},
        positions=[("NVDA", "0.0008"), ("MSFT", "5.0"), ("GME", "0.0001")],  # GME = personal
    )
    _use_client(monkeypatch, client)

    res = executor.dust_cleanup(mid, dry_run=True)

    assert res["ok"] is True and res["will_place"] is False
    assert [o["symbol"] for o in res["orders"]] == ["NVDA"]
    entry = res["orders"][0]
    assert entry["status"] == "reviewed"
    assert float(entry["qty"]) == pytest.approx(0.0008)
    assert entry["value"] == pytest.approx(0.08)
    assert client.trades_for("review_sell", f"ACC{mid}", "NVDA")
    assert not client.trades_for("place_sell", f"ACC{mid}")
    # dry run records nothing: still only the seeded 'placed' row for NVDA
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM executions WHERE member_id=? AND symbol='NVDA'",
                         (mid,)).fetchone()[0]
    assert n == 1


# ─── A8: skip_reason visibility ──────────────────────────────────────────────

def test_opted_out_member_appears_with_skip_reason():
    """Opted-OUT humans get a summary row (skip_reason='opted_out') + an audit
    entry; unconnected voters get 'not_connected'."""
    voter = _member("a8-voter@x.com", connect=False)
    outsider = _member("a8-optout@x.com", opted_in=0, connect=False)
    closed = _closed_session(datetime.date(2026, 12, 9), voter, ["AAPL"])

    summary = executor.run(closed, force_dry_run=True)

    out_row = _mrow(summary, outsider)
    assert out_row["skip_reason"] == "opted_out"
    assert out_row["orders"] == []
    assert {"member_id": outsider, "reason": "opted_out"} in _skip_audits()
    voter_row = _mrow(summary, voter)
    assert voter_row["skip_reason"] == "not_connected"
    assert voter_row["note"] == "no connected agentic Robinhood account"
    assert {"member_id": voter, "reason": "not_connected"} in _skip_audits()


# ─── post-trade account verification (services/verify.py) ─────────────────────

from app.services import verify  # noqa: E402


def test_verify_clean_account_passes(monkeypatch):
    """Club holds AAPL (still in the basket), account matches, no errors → PASS."""
    mid = _member("verify-clean@x.com")
    executor._record(95001, mid, "AAPL", 200.0, "vref-1", "placed", place={"_concord_est_qty": 1.0})
    _closed_session(datetime.date(2026, 12, 20), mid, ["AAPL"])  # AAPL is the current basket
    client = FakeClient(quotes={"AAPL": 200.0}, positions=[("AAPL", "1.0")])
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid)
    assert res["verdict"] == "PASS"
    assert any(f["code"] == "clean" for f in res["findings"])


def test_verify_errored_order_fails(monkeypatch):
    """An execution row the club recorded as errored surfaces as a FAIL."""
    mid = _member("verify-err@x.com")
    closed = _closed_session(datetime.date(2027, 3, 16), mid, ["AAPL"])
    executor._record(closed["id"], mid, "MSFT", None, "vref-2", "error",
                     error="sell: Order quantity cannot include fractional shares", side="sell")
    client = FakeClient(quotes={"AAPL": 200.0}, positions=[])
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid)
    assert res["verdict"] == "FAIL"
    assert any(f["code"] == "errored_order" and "MSFT" in f["msg"] for f in res["findings"])


def test_verify_flat_flags_leftover(monkeypatch):
    """expect_flat: a club name still held above dust means NOT fully sold → FAIL."""
    mid = _member("verify-flat-bad@x.com")
    executor._record(95003, mid, "AAPL", 200.0, "vref-3", "placed", place={"_concord_est_qty": 1.0})
    client = FakeClient(quotes={"AAPL": 200.0}, positions=[("AAPL", "1.0")])  # $200 still held
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid, expect_flat=True)
    assert res["verdict"] == "FAIL"
    assert any(f["code"] == "not_flat" and "AAPL" in f["msg"] for f in res["findings"])


def test_verify_flat_ignores_dust(monkeypatch):
    """expect_flat: a sub-$1 crumb is broker dust, not an un-sold holding → PASS."""
    mid = _member("verify-flat-ok@x.com")
    executor._record(95004, mid, "AAPL", 200.0, "vref-4", "placed", place={"_concord_est_qty": 1.0})
    client = FakeClient(quotes={"AAPL": 200.0}, positions=[("AAPL", "0.001")])  # $0.20 dust
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid, expect_flat=True)
    assert res["verdict"] == "PASS"
    assert any(f["code"] == "flat" for f in res["findings"])


def test_verify_flags_dropped_name_not_sold(monkeypatch):
    """A name DROPPED from the current basket but still held = a sell that didn't
    complete → FAIL exit_incomplete."""
    mid = _member("verify-stuck@x.com")
    executor._record(95005, mid, "TSLA", 250.0, "vref-5", "placed", place={"_concord_est_qty": 1.0})
    _closed_session(datetime.date(2026, 12, 22), mid, ["AAPL"])  # basket = AAPL; TSLA dropped
    client = FakeClient(quotes={"AAPL": 200.0, "TSLA": 250.0}, positions=[("TSLA", "1.0")])
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid)
    assert res["verdict"] == "FAIL"
    assert any(f["code"] == "exit_incomplete" and "TSLA" in f["msg"] for f in res["findings"])


def test_verify_uses_newest_basket_not_stale_executed(monkeypatch):
    """Regression: on a sell day the just-closed session (new basket) must win over
    the prior 'executed' session (old basket that still lists the dropped name).
    Old code (`executed or closed`) picked the stale basket and PASSed a held drop."""
    mid = _member("verify-stale@x.com")
    executor._record(95006, mid, "TSLA", 250.0, "vref-6", "placed", place={"_concord_est_qty": 1.0})
    # PRIOR cycle: executed, basket still contains TSLA
    prior = _closed_session(datetime.date(2026, 12, 23), mid, ["TSLA", "AAPL"])
    with db() as conn:
        conn.execute("UPDATE vote_sessions SET status='executed' WHERE id=?", (prior["id"],))
    # THIS cycle: newer closed session, TSLA dropped
    _closed_session(datetime.date(2026, 12, 24), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 200.0, "TSLA": 250.0}, positions=[("TSLA", "1.0")])
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid)
    assert res["verdict"] == "FAIL"   # newest basket = {AAPL}; TSLA dropped but held
    assert any(f["code"] == "exit_incomplete" and "TSLA" in f["msg"] for f in res["findings"])


def test_verify_unpriced_holding_is_not_waved_through(monkeypatch):
    """Regression: a held club position with NO quote must NOT collapse to $0 and be
    dismissed as dust — that returned a false PASS on an un-sold liquidation."""
    mid = _member("verify-unpriced@x.com")
    executor._record(95007, mid, "AAPL", 200.0, "vref-7", "placed", place={"_concord_est_qty": 1.0})
    client = FakeClient(quotes={}, positions=[("AAPL", "1.0")])  # AAPL held, NO quote
    _use_client(monkeypatch, client)

    res = verify.verify_member(mid, expect_flat=True)
    assert res["verdict"] == "FAIL"
    assert any(f["code"] == "unpriced_holding" and "AAPL" in f["msg"] for f in res["findings"])


# ─── club_shares re-architecture: feed-derived position (services/positions.py) ──

from app.services import positions  # noqa: E402


def _ord(sym, side, agent, cum):
    return {"symbol": sym, "side": side, "placed_agent": agent, "cumulative_quantity": str(cum),
            "created_at": "2026-01-01T14:30:00Z"}


def test_derive_clean_and_net():
    orders = [_ord("AAPL", "buy", "agentic", 2.0), _ord("AAPL", "buy", "agentic", 1.0),
              _ord("AAPL", "sell", "agentic", 1.0)]
    assert positions.club_position_from_orders(orders) == {"AAPL": 2.0}


def test_derive_partial_fill_counts_cumulative():
    # partially_filled: cumulative 6 of a 10-share order → counts 6
    assert positions.club_position_from_orders([_ord("AAPL", "buy", "agentic", 6.0)])["AAPL"] == 6.0


def test_derive_rejected_contributes_zero():
    assert "AAPL" not in positions.club_position_from_orders([_ord("AAPL", "buy", "agentic", 0.0)])


def test_derive_cancelled_after_partial_sell_still_subtracts():
    orders = [_ord("X", "buy", "agentic", 10), _ord("X", "sell", "agentic", 4)]  # cancelled sell, cum 4
    assert positions.club_position_from_orders(orders) == {"X": 6.0}


def test_derive_personal_buy_excluded_none_and_user():
    # asymmetric rule: BUY counts ONLY agentic
    assert positions.club_position_from_orders([_ord("T", "buy", "user", 5)]) == {}
    assert positions.club_position_from_orders([_ord("T", "buy", None, 5)]) == {}


def test_derive_sell_rule_user_ignored_none_subtracted():
    # SELL subtracts everything EXCEPT 'user'
    assert positions.club_position_from_orders(
        [_ord("X", "buy", "agentic", 10), _ord("X", "sell", "user", 5)]) == {"X": 10.0}
    assert positions.club_position_from_orders(
        [_ord("X", "buy", "agentic", 10), _ord("X", "sell", None, 4)]) == {"X": 6.0}


def test_derived_intersects_club_traded_symbols(monkeypatch):
    """A feed order on a symbol the club NEVER placed (a broker mis-tag) is dropped;
    a real club-traded symbol is kept."""
    mid = _member("derive-intersect@x.com")
    executor._record(96001, mid, "AAPL", 200.0, "dref-1", "placed", place={"_concord_est_qty": 1.0})
    client = FakeClient(orders=[_ord("AAPL", "buy", "agentic", 1.0),
                                _ord("ZZZZ", "buy", "agentic", 9.0)])  # ZZZZ never club-traded
    _use_client(monkeypatch, client)
    positions.invalidate_feed_cache()
    assert positions.club_shares_derived(mid) == {"AAPL": 1.0}


def test_derived_read_failure_raises_incomplete(monkeypatch):
    """A feed read error raises DerivationIncomplete (caller fails closed) — never
    a silent empty that would strand sells / re-buy the basket."""
    mid = _member("derive-fail@x.com")
    executor._record(96002, mid, "AAPL", 200.0, "dref-2", "placed", place={"_concord_est_qty": 1.0})
    client = FakeClient(orders_exc=RuntimeError("MCP HTTP 500"))
    _use_client(monkeypatch, client)
    positions.invalidate_feed_cache()
    with pytest.raises(positions.DerivationIncomplete):
        positions.club_shares_derived(mid)


# ─── Same-day 'both' runs: buy leg must see the morning's sale proceeds ──────

class ProceedsClient(FakeClient):
    """buying_power jumps only after a sell has been placed — models
    limited-margin proceeds becoming spendable once the 9:31 sells fill."""

    def __init__(self, *, bp_after_sell, **kw):
        super().__init__(**kw)
        self.bp_after_sell = bp_after_sell
        self.sold = False

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.sold = True
        return super().place_market_sell(acct, symbol, qty, ref_id=ref_id)

    def get_portfolio(self, acct):
        if self.sold:
            self.buying_power = self.bp_after_sell
        return super().get_portfolio(acct)


def test_both_day_refreshes_buying_power_after_sells(monkeypatch):
    """Live regression (first mwf both-day, 2026-08-19): the pre-sell portfolio
    snapshot hid the morning's sale proceeds, so every buy deferred with
    'beyond settled buying power' — even for limited-margin members whose
    proceeds ARE immediately spendable. After placing sells in a 'both' run,
    the buy leg must re-fetch buying power and size against the fresh number."""
    from app.config import settings
    from app.db import set_armed

    mid = _member("bothday@x.com", alloc_value=50.0)
    executor._record(90050, mid, "TSLA", 250.0, "bothday-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90050, mid)
    closed = _closed_session(datetime.date(2026, 12, 28), mid, ["AAPL"])  # TSLA dropped
    client = ProceedsClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                            positions=[("TSLA", "1.0")],
                            buying_power=0.40, total_value=250.0,
                            bp_after_sell=250.0)
    _use_client(monkeypatch, client)

    prev_mode = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    # single-name test basket: waive the thin-basket floor (G2) — this test is
    # about buying-power mechanics, not basket breadth (conftest restores)
    object.__setattr__(settings, "min_basket_size", 1)
    # ...and disable the FRACTIONAL floor: these tests deliberately trade a
    # 1-2 name basket at TOP_N=3, which ceil(3 * 0.6) = 2 would otherwise block.
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False, phase="both")
    finally:
        object.__setattr__(settings, "execution_mode", prev_mode)
        set_armed(False)

    assert client.trades_for("place_sell", f"ACC{mid}", "TSLA"), "sell leg placed"
    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL"), \
        "buy must place against refreshed buying power (regression: deferred)"
    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "AAPL" and o["side"] == "buy")
    assert order["status"] == "placed"
    assert len([c for c in client.calls if c[0] == "get_portfolio"]) >= 2, \
        "buy leg re-fetched the portfolio after the sells"


def test_sell_only_phase_does_not_refetch_portfolio(monkeypatch):
    """The refresh is scoped to same-day 'both' runs — a plain sell-day run
    keeps the single portfolio fetch (no extra broker calls)."""
    mid = _member("sellonly@x.com", alloc_value=50.0)
    executor._record(90051, mid, "TSLA", 250.0, "sellonly-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    closed = _closed_session(datetime.date(2026, 12, 29), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "1.0")], buying_power=100.0)
    _use_client(monkeypatch, client)

    executor.run(closed, force_dry_run=True, phase="sell")

    # scope to THIS member's account — the shared FakeClient serves every
    # member the run touches (same caveat as trades_for)
    mine = [c for c in client.calls if c[0] == "get_portfolio" and c[1] == f"ACC{mid}"]
    assert len(mine) == 1


class LaggyProceedsClient(FakeClient):
    """buying_power rises only on the Nth get_portfolio call AFTER a sell —
    models the real 08/19 failure: Robinhood credits proceeds well after the
    market sells place (fill + ledger lag), so a single quick re-fetch still
    sees the stale number and every buy defers."""

    def __init__(self, *, bp_after_sell, credit_after_fetches, **kw):
        super().__init__(**kw)
        self.bp_after_sell = bp_after_sell
        self.credit_after_fetches = credit_after_fetches
        self.sold = False
        self.fetches_since_sell = 0

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        self.sold = True
        return super().place_market_sell(acct, symbol, qty, ref_id=ref_id)

    def get_portfolio(self, acct):
        if self.sold:
            self.fetches_since_sell += 1
            if self.fetches_since_sell >= self.credit_after_fetches:
                self.buying_power = self.bp_after_sell
        return super().get_portfolio(acct)


def test_both_day_polls_past_slow_proceeds_credit(monkeypatch):
    """Second live regression (both-day 2026-08-19, AFTER the first fix): a
    single ~4s re-fetch still read stale buying power because the credit lands
    tens of seconds after the sells place. The buy leg must POLL until the
    proceeds show up (bounded by BP_POLL_MAX_S) and then place."""
    from app.config import settings
    from app.db import set_armed

    mid = _member("bothlag@x.com", alloc_value=50.0)
    executor._record(90060, mid, "TSLA", 250.0, "bothlag-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90060, mid)
    closed = _closed_session(datetime.date(2026, 12, 30), mid, ["AAPL"])
    # stale reads for the first 3 post-sell fetches, credit on the 4th
    client = LaggyProceedsClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                                 positions=[("TSLA", "1.0")],
                                 buying_power=0.40, total_value=250.0,
                                 bp_after_sell=250.40, credit_after_fetches=4)
    _use_client(monkeypatch, client)

    prev_mode = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    # single-name test basket: waive the thin-basket floor (G2) — this test is
    # about buying-power mechanics, not basket breadth (conftest restores)
    object.__setattr__(settings, "min_basket_size", 1)
    # ...and disable the FRACTIONAL floor: these tests deliberately trade a
    # 1-2 name basket at TOP_N=3, which ceil(3 * 0.6) = 2 would otherwise block.
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False, phase="both")
    finally:
        object.__setattr__(settings, "execution_mode", prev_mode)
        set_armed(False)

    assert client.trades_for("place_buy", f"ACC{mid}", "AAPL"), \
        "buy must place once the poll finally sees the credited proceeds"
    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "AAPL" and o["side"] == "buy")
    assert order["status"] == "placed"


def test_run_sweeps_dust_per_member_with_matching_dry_run(monkeypatch):
    """dust_cleanup existed only behind a manual admin button, so 0.000001-share
    crumbs (Neil's MSFT, Charlie's ALL) sat for days. run() now sweeps each
    non-skipped member after the buy leg, with dry_run mirroring will_place —
    a review-only run must preview the sweep, never place it.

    run() now also threads its executing session into the sweep (session_id=sid),
    so every ref_id, guard and record keys to the session that actually trades —
    the stub has to accept it.
    """
    calls: list[tuple[int, bool, int | None]] = []
    monkeypatch.setattr(executor, "dust_cleanup",
                        lambda mid, dry_run=True, session_id=None:
                        calls.append((mid, dry_run, session_id)) or
                        {"ok": True, "orders": []})
    mid = _member("dusthook@x.com", alloc_value=50.0)
    closed = _closed_session(datetime.date(2026, 12, 18), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 100.0}, buying_power=50.0)
    _use_client(monkeypatch, client)
    executor.run(closed, force_dry_run=True, phase="both")
    assert (mid, True, closed["id"]) in calls, \
        "dry run → dust preview (dry_run=True) keyed to the executing session"


def test_limited_margin_buys_what_it_can_afford_instead_of_dropping_the_name(monkeypatch):
    """Observed 2026-09-03: ADM was skipped outright on two limited-margin
    accounts, one of them short by $0.02 of a $5.45 top-up — the whole position
    dropped for two cents, and every cycle it was the same (last) name. On an
    account that settles instantly the shortfall is buyable next cycle, so
    buying what IS affordable and carrying the remainder beats holding nothing.
    """
    from app.config import settings
    from app.db import kv_set, set_armed

    mid = _member("bothlm@x.com", alloc_value=50.0)
    kv_set(f"acct_type:{mid}", "limited_margin")
    executor._record(90071, mid, "TSLA", 250.0, "bothlm-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90071, mid)
    closed = _closed_session(datetime.date(2026, 12, 17), mid, ["AAPL"])
    # Credit never fully lands: buying power stops a hair under the target, the
    # exact shape that dropped ADM.
    client = LaggyProceedsClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                                 positions=[("TSLA", "1.0")], buying_power=3.78,
                                 bp_after_sell=48.0, credit_after_fetches=1)
    _use_client(monkeypatch, client)

    prev_mode = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 1)
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False, phase="both")
    finally:
        object.__setattr__(settings, "execution_mode", prev_mode)
        set_armed(False)

    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "AAPL" and o["side"] == "buy")
    assert order["status"] == "placed", "an affordable remainder must still buy the name"
    assert float(order["amount"]) <= 48.0, "never spends more than the credited buying power"
    assert "partial" in (order.get("note") or ""), "a short buy must say it was short"


def test_both_day_cash_account_keeps_single_quick_look(monkeypatch):
    """A cash member's buying power is settled-only and never moves same-day —
    the poll must not burn its full window on them (kv acct_type = cash keeps
    one post-sell look, and the buy defers exactly as before)."""
    from app.config import settings
    from app.db import kv_set, set_armed

    mid = _member("bothcash@x.com", alloc_value=50.0)
    kv_set(f"acct_type:{mid}", "cash")
    executor._record(90061, mid, "TSLA", 250.0, "bothcash-ref-1", "placed",
                     place={"_concord_est_qty": 1.0})
    _backdate(90061, mid)
    closed = _closed_session(datetime.date(2026, 12, 31), mid, ["AAPL"])
    client = LaggyProceedsClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                                 positions=[("TSLA", "1.0")], buying_power=3.78,
                                 bp_after_sell=253.0, credit_after_fetches=3)
    _use_client(monkeypatch, client)

    prev_mode = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    # single-name test basket: waive the thin-basket floor (G2) — this test is
    # about buying-power mechanics, not basket breadth (conftest restores)
    object.__setattr__(settings, "min_basket_size", 1)
    # ...and disable the FRACTIONAL floor: these tests deliberately trade a
    # 1-2 name basket at TOP_N=3, which ceil(3 * 0.6) = 2 would otherwise block.
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False, phase="both")
    finally:
        object.__setattr__(settings, "execution_mode", prev_mode)
        set_armed(False)

    order = next(o for o in _mrow(summary, mid)["orders"]
                 if o["symbol"] == "AAPL" and o["side"] == "buy")
    assert order["status"] == "skipped", "cash buys defer to the next buy day"
    mine = [c for c in client.calls if c[0] == "get_portfolio" and c[1] == f"ACC{mid}"]
    assert len(mine) == 2, "pre-sell snapshot + ONE quick post-sell look, no long poll"


# ─── run_reports persistence: every run leaves a row ─────────────────────────

def _latest_report() -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM run_reports ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "no run_reports row written"
    return dict(row)


def test_run_persists_report_row_with_counts(monkeypatch):
    """executor.run() writes a run_reports row: trigger threaded through, the
    identity columns, counts over THIS run's member orders, and a summary_json
    that parses back to the full summary."""
    mid = _member("rr-counts@x.com")
    closed = _closed_session(datetime.date(2027, 1, 4), mid, ["AAPL", "MSFT"])
    client = FakeClient(quotes={"AAPL": 100.0})   # MSFT unquoted → its buy skips
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, trigger="manual:owner@x.com")

    rep = _latest_report()
    assert rep["trigger"] == "manual:owner@x.com"
    assert rep["trade_date"] == closed["trade_date"] == "2027-01-04"
    assert rep["phase"] == "both" and rep["mode"] == summary["mode"]
    assert rep["will_place"] == 0
    assert rep["started_at"] > 0 and rep["finished_at"] >= rep["started_at"]
    # counts are over THIS run's member orders (the shared test DB carries
    # opted-in members from other modules, so compare against the summary
    # itself, not absolutes). Our member contributes ≥1 skip (MSFT, no quote);
    # dry-run 'reviewed' orders are not counted.
    want = {"placed": 0, "skipped": 0, "error": 0}
    for o in summary["orders"]:
        if o["status"] in want:
            want[o["status"]] += 1
    assert (rep["placed"], rep["skipped"], rep["errors"]) == \
        (want["placed"], want["skipped"], want["error"])
    assert rep["skipped"] >= 1 and rep["placed"] == 0 and rep["errors"] == 0
    parsed = json.loads(rep["summary_json"])
    assert parsed["picks"] == summary["picks"]
    my_row = next(m for m in parsed["members"] if m["member_id"] == mid)
    assert [o["symbol"] for o in my_row["orders"] if o["status"] == "skipped"] == ["MSFT"]


def test_run_default_trigger_is_schedule(monkeypatch):
    mid = _member("rr-sched@x.com")
    closed = _closed_session(datetime.date(2027, 1, 5), mid, ["AAPL"])
    _use_client(monkeypatch, FakeClient(quotes={"AAPL": 100.0}))
    executor.run(closed, force_dry_run=True)
    assert _latest_report()["trigger"] == "schedule"


def test_no_op_run_still_persists_report(monkeypatch):
    """A no-picks run writes a row too — the absence of a record is itself a
    gap. The wrapper backfills the identity fields the early return omits."""
    mid = _member("rr-noop@x.com")
    s = voting.open_session_for(datetime.date(2027, 1, 6))   # nobody votes
    closed = voting.close_and_select(s["id"])

    res = executor.run(closed, force_dry_run=True, trigger="manual:x@y.z")

    assert "no consensus picks" in res["note"]
    rep = _latest_report()
    assert rep["trigger"] == "manual:x@y.z"
    assert rep["trade_date"] == "2027-01-06" and rep["phase"] == "both"
    assert (rep["placed"], rep["skipped"], rep["errors"]) == (0, 0, 0)
    assert json.loads(rep["summary_json"])["note"] == res["note"]


def test_run_today_idle_day_persists_schedule_report(monkeypatch):
    """The timer firing on an idle day is still a run — the row (phase 'none',
    trigger 'schedule') is the proof it fired and correctly did nothing."""
    import app.calendar_us as cal
    monkeypatch.setattr(cal, "cycle_phase", lambda d: None)

    res = executor.run_today(force_dry_run=True)

    assert "idle day" in res["note"]
    rep = _latest_report()
    assert rep["trigger"] == "schedule" and rep["phase"] == "none"
    assert rep["will_place"] == 0
    assert (rep["placed"], rep["skipped"], rep["errors"]) == (0, 0, 0)
    assert json.loads(rep["summary_json"])["note"] == res["note"]


def test_report_persistence_failure_never_fails_the_run(monkeypatch):
    """The hard rule: a run must NEVER fail because its report could not be
    written (the run may have just placed real orders)."""
    from app.services import runreports
    mid = _member("rr-dbfail@x.com")
    closed = _closed_session(datetime.date(2027, 1, 7), mid, ["AAPL"])
    _use_client(monkeypatch, FakeClient(quotes={"AAPL": 100.0}))

    def _boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(runreports, "db", _boom)

    summary = executor.run(closed, force_dry_run=True)     # must not raise
    assert summary["picks"] == ["AAPL"]
    assert any(o["symbol"] == "AAPL" and o["status"] == "reviewed"
               for o in summary["orders"])


def test_report_insert_failure_never_fails_the_run(monkeypatch):
    """Same hard rule, one layer deeper: the INSERT statement itself raising
    (table gone / disk error mid-write, not just the connection factory) is
    swallowed too — the try/except wraps the write, not only the open."""
    mid = _member("rr-insfail@x.com")
    closed = _closed_session(datetime.date(2027, 1, 8), mid, ["AAPL"])
    _use_client(monkeypatch, FakeClient(quotes={"AAPL": 100.0}))

    with db() as conn:
        conn.execute("ALTER TABLE run_reports RENAME TO run_reports_hidden")
    try:
        summary = executor.run(closed, force_dry_run=True)  # must not raise
        assert summary["picks"] == ["AAPL"]
        assert any(o["symbol"] == "AAPL" and o["status"] == "reviewed"
                   for o in summary["orders"])
    finally:
        with db() as conn:
            conn.execute("ALTER TABLE run_reports_hidden RENAME TO run_reports")


# ─── small-stake resize: exit + re-enter when a top-up is unplaceable ────────

def _pin_picks(sid: int, picks: list[str]) -> dict:
    """Pin a session's consensus list verbatim and return the refreshed row.

    The n_eff tests are about RANK, and close_and_select's ordering of equally
    weighted picks is not something they should depend on — nor is TOP_N, since
    the point is a pick list longer than the member can hold. run() reads picks
    off the session DICT it is handed, so the caller must use the row returned
    here, not the one close_and_select produced."""
    with db() as conn:
        conn.execute("UPDATE vote_sessions SET picks_json=? WHERE id=?",
                     (json.dumps([{"symbol": s} for s in picks]), sid))
        return dict(conn.execute("SELECT * FROM vote_sessions WHERE id=?", (sid,)).fetchone())


def test_held_name_inside_the_hysteresis_band_keeps_its_slot(monkeypatch):
    """A small stake holds the TOP n_eff names, and the club re-ranks daily — so
    a held name drifting one rank past n_eff used to be sold and a new name
    bought in its place, a full round trip paying the spread on both legs, most
    cycles. A held name now keeps its slot while it stays within n_eff + H of
    the top, and the buy leg fills only the slots that are actually free.
    """
    mid = _member("hysteresis-keep@x.com", alloc_value=10.0)   # 10% of $25 = $2.50
    closed = _closed_session(datetime.date(2027, 3, 1), mid, ["AAPL"])
    sid = closed["id"]
    # E sits at rank 5 — outside the top 2 the member can afford, inside the band
    picks = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA"]
    closed = _pin_picks(sid, picks)
    executor._record(sid - 1 if sid > 1 else 90900, mid, "NVDA", 1.25, "hyst-keep-ref",
                     "placed", place={"_concord_est_qty": 0.0125})
    _backdate(sid - 1 if sid > 1 else 90900, mid)
    client = FakeClient(quotes={s: 100.0 for s in picks},
                        positions=[("NVDA", "0.0125")],
                        buying_power=5.0, total_value=25.0)
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, phase="both")

    row = _mrow(summary, mid)
    assert row["budget"] == 2.50 and row["per_pick"] == 1.25
    touched = {o["symbol"] for o in row["orders"]}
    assert "NVDA" in touched, "the held name stays in the basket"
    assert not [o for o in row["orders"] if o["symbol"] == "NVDA" and o["side"] == "sell"], \
        "a held name inside the band is never round-tripped"
    assert "MSFT" not in touched, "rank 2 does not displace it — the slot is not free"
    aapl = next(o for o in row["orders"] if o["symbol"] == "AAPL")
    assert aapl["side"] == "buy" and aapl["status"] == "reviewed", aapl


def test_held_name_beyond_the_hysteresis_band_rotates_out(monkeypatch):
    """The band is BOUNDED on purpose: keeping every held name that lingers
    anywhere in the club's list would ossify a small member's basket and stop
    them ever rotating into the club's new conviction. Past n_eff + H the held
    name is exited and the top names take the slots."""
    mid = _member("hysteresis-evict@x.com", alloc_value=10.0)
    closed = _closed_session(datetime.date(2027, 3, 2), mid, ["AAPL"])
    sid = closed["id"]
    # JPM sits at rank 8 — well past the n_eff(2) + H(4) band
    picks = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM"]
    closed = _pin_picks(sid, picks)
    executor._record(90901, mid, "JPM", 1.25, "hyst-evict-ref", "placed",
                     place={"_concord_est_qty": 0.0125})
    _backdate(90901, mid)
    client = FakeClient(quotes={s: 100.0 for s in picks},
                        positions=[("JPM", "0.0125")],
                        buying_power=5.0, total_value=25.0)
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True, phase="both")

    row = _mrow(summary, mid)
    jpm = next(o for o in row["orders"] if o["symbol"] == "JPM")
    assert jpm["side"] == "sell" and jpm["status"] == "reviewed", jpm
    assert jpm.get("note") == "exited basket"
    bought = {o["symbol"] for o in row["orders"] if o["side"] == "buy"}
    assert bought == {"AAPL", "MSFT"}, bought


def test_sub_minimum_topup_resizes_by_selling_and_rebuying(monkeypatch):
    """Charlie's live shape (2026-08-24): a ~$1.30 per-name target with $1.02
    held. The $0.28 top-up is under Robinhood's $1 minimum, so before this the
    name was reported 'at target' and could never be corrected. The position is
    now exited whole and re-bought at the right size."""
    from app.config import settings
    from app.db import set_armed

    mid = _member("resize@x.com", alloc_value=100.0)
    # club ledger: 1.02 shares of a $1.00 stock == $1.02 held
    executor._record(90600, mid, "AAPL", 1.02, "resize-ref", "placed",
                     place={"_concord_est_qty": 1.02})
    _backdate(90600, mid)
    closed = _closed_session(datetime.date(2027, 6, 15), mid, ["AAPL"])
    client = FakeClient(quotes={"AAPL": 1.00}, positions=[("AAPL", "1.02")],
                        buying_power=1.30, total_value=1.30)
    _use_client(monkeypatch, client)

    prev = settings.execution_mode
    object.__setattr__(settings, "execution_mode", "live")
    object.__setattr__(settings, "min_basket_size", 1)
    # ...and disable the FRACTIONAL floor: these tests deliberately trade a
    # 1-2 name basket at TOP_N=3, which ceil(3 * 0.6) = 2 would otherwise block.
    object.__setattr__(settings, "min_basket_fraction", 0.0)
    set_armed(True)
    try:
        summary = executor.run(closed, force_dry_run=False, phase="both")
    finally:
        object.__setattr__(settings, "execution_mode", prev)
        set_armed(False)

    orders = _mrow(summary, mid)["orders"]
    sell = next((o for o in orders if o["side"] == "sell" and o["symbol"] == "AAPL"), None)
    buy = next((o for o in orders if o["side"] == "buy" and o["symbol"] == "AAPL"), None)
    assert sell and sell["status"] == "placed", "under-target name must be exited whole"
    assert "resize" in (sell.get("note") or ""), sell
    assert buy and buy["status"] == "placed", \
        "and re-bought at target (regression: skipped as 'at target' forever)"


def test_cash_buffer_no_longer_strands_a_fully_committed_account():
    """Neil (2026-08-24): stake == 100% of the account, so a 1% reserve left
    less deployable than the budget and the last name deferred every cycle."""
    from app.config import settings
    assert settings.cash_buffer_pct == 0.0
    assert executor.deployable(1000.0) == 1000.0


# ─── the unsellable-microshare fix (Charlie's ALL, 2026-09-10) ───────────────
# A month of rejected 0.000001-share sells traced to two defects: the raw
# 6dp floor chopped a full-position 0.00399 to 0.003989 (float representation
# error minting an unsellable crumb), and both sell paths then ordered the
# crumb daily — which Robinhood rejects onto the member's phone.

def test_floor_qty6_is_float_safe():
    # the bug: math.floor(0.00399*1e6)/1e6 == 0.003989 — the epsilon repairs it
    assert executor.floor_qty6(0.00399) == 0.00399
    assert executor.floor_qty6(0.003990) == 0.00399
    # a genuinely sub-6dp quantity still floors DOWN, never up
    assert executor.floor_qty6(0.0039896) == 0.003989
    assert executor.floor_qty6(0.0039899999) == 0.003989
    # boundary crumbs
    assert executor.floor_qty6(0.000001) == 0.000001
    assert executor.floor_qty6(0.0) == 0.0


def test_min_sell_qty_covers_truncation_residues_but_not_real_dust():
    # truncation residues are ≤ 1e-6 by construction → always below the floor
    assert 0.000001 < executor.MIN_SELL_QTY
    # real dust (a $0.30 crumb at a $250 stock ≈ 0.0012 sh) stays sweepable
    assert 0.0012 > executor.MIN_SELL_QTY


class _CrumbClient:
    """A client for crumb scenarios: SELL review of a crumb is forbidden
    (unsellable — the old bug ordered it for a month), BUY review/get_portfolio
    serve the repair path."""

    def __init__(self, bp=100.0):
        self.bp = bp
        self.buy_reviews: list[tuple] = []

    def get_quotes(self, symbols):
        return {"data": {"results": [{"quote": {"symbol": s, "last_trade_price": "254.00"}}
                                     for s in symbols]}}

    def get_portfolio(self, acct):
        return {"data": {"buying_power": {"buying_power": str(self.bp)}}}

    def review_market_sell(self, *a, **k):
        raise AssertionError("an unsellable residue must never be sell-reviewed")

    def review_market_buy(self, acct, symbol, amt):
        self.buy_reviews.append((symbol, amt))
        return {"data": {"order_checks": {}}}


def _crumb_setup(monkeypatch, email: str, *, picks: list[str], date: datetime.date,
                 bp: float = 100.0) -> tuple[int, "_CrumbClient"]:
    mid = _member(email)
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id, member_id, symbol, side, type, dollar_amount, "
            "ref_id, status, created_at) VALUES(1, ?, 'ALL', 'buy', 'market', 1.0, ?, "
            "'placed', strftime('%s','now'))", (mid, f"seed-{mid}"))
    # a fresh NEWEST session pins latest_sid + the current basket deterministically
    _closed_session(date, mid, picks)
    client = _CrumbClient(bp=bp)
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    monkeypatch.setattr(executor.positions, "live_positions",
                        lambda m: [{"symbol": "ALL", "quantity": 0.000001}])
    return mid, client


def test_unsellable_residue_gets_repaired_not_sold(monkeypatch):
    """A 1e-6 crumb is never sell-reviewed; instead the sweep plans the
    broker-minimum top-up BUY (next cycle's sell leg exits the whole
    position)."""
    mid, client = _crumb_setup(monkeypatch, "crumb@example.com",
                               picks=["MSFT"], date=datetime.date(2026, 12, 13))
    out = executor.dust_cleanup(mid, dry_run=True)
    assert out["ok"] is True
    entries = [o for o in out["orders"] if o["symbol"] == "ALL"]
    assert entries and entries[0]["side"] == "buy"
    assert entries[0]["status"] == "reviewed" and entries[0]["amount"] == "1.00"
    assert client.buy_reviews == [("ALL", "1.00")]


def test_crumb_on_basket_name_is_left_to_the_buy_leg(monkeypatch):
    """If the crumb's name is IN the current basket AND this member's own buy leg
    reached it, the ordinary top-up absorbs the crumb — no repair order.

    Contract change: in-basket alone is no longer enough to defer. The skip now
    also requires the member's OWN plain buy row for the session (ref_id_for(sid,
    mid, symbol), placed or reviewed) — that row is the evidence an absorbing
    top-up actually exists. See the sibling test for the small-stake case where
    it does not.
    """
    mid, client = _crumb_setup(monkeypatch, "crumb-basket@example.com",
                               picks=["ALL"], date=datetime.date(2026, 12, 14))
    with db() as conn:
        sid = conn.execute("SELECT MAX(id) FROM vote_sessions").fetchone()[0]
    # this member's buy leg did reach ALL this cycle — the absorbing top-up
    executor._record(sid, mid, "ALL", 1.30, executor.ref_id_for(sid, mid, "ALL"),
                     "placed", place={"_concord_est_qty": 0.005})
    out = executor.dust_cleanup(mid, dry_run=True)
    entries = [o for o in out["orders"] if o["symbol"] == "ALL"]
    assert entries and entries[0]["status"] == "skipped"
    assert "buy leg absorbs" in entries[0]["note"]
    assert client.buy_reviews == []


def test_crumb_on_basket_name_without_own_buy_is_repaired(monkeypatch):
    """The other half of that contract: a small-stake member whose picks[:n_eff]
    slice never reaches the name has NO buy row to absorb the crumb, so deferring
    to "the buy leg" would strand it forever — the repair fires instead."""
    mid, client = _crumb_setup(monkeypatch, "crumb-basket-nobuy@example.com",
                               picks=["ALL"], date=datetime.date(2026, 12, 15))
    out = executor.dust_cleanup(mid, dry_run=True)
    entries = [o for o in out["orders"] if o["symbol"] == "ALL"]
    assert entries and entries[0]["side"] == "buy"
    assert entries[0]["status"] == "reviewed" and entries[0]["amount"] == "1.00"
    assert client.buy_reviews == [("ALL", "1.00")]


def test_crumb_repair_needs_buying_power(monkeypatch):
    """No repair on an account without headroom — skip with the reason."""
    mid, client = _crumb_setup(monkeypatch, "crumb-broke@example.com",
                               picks=["MSFT"], date=datetime.date(2026, 12, 16), bp=0.50)
    out = executor.dust_cleanup(mid, dry_run=True)
    entries = [o for o in out["orders"] if o["symbol"] == "ALL"]
    assert entries and entries[0]["status"] == "skipped"
    assert "buying power" in entries[0]["note"]
    assert client.buy_reviews == []


def test_crumb_repair_idempotent_within_a_session(monkeypatch):
    """A second sweep in the same session sees the DUSTFIX row and does not
    review a second buy."""
    mid, client = _crumb_setup(monkeypatch, "crumb-again@example.com",
                               picks=["MSFT"], date=datetime.date(2026, 12, 19))
    with db() as conn:
        sid = conn.execute("SELECT MAX(id) FROM vote_sessions").fetchone()[0]
    fix_ref = executor.ref_id_for(sid, mid, "DUSTFIX:ALL")
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id, member_id, symbol, side, type, dollar_amount, "
            "ref_id, status, created_at) VALUES(?, ?, 'ALL', 'buy', 'market', 1.0, ?, "
            "'reviewed', strftime('%s','now'))", (sid, mid, fix_ref))
    out = executor.dust_cleanup(mid, dry_run=True)
    entries = [o for o in out["orders"] if o["symbol"] == "ALL"]
    assert entries and entries[0]["status"] == "skipped"
    assert "already attempted" in entries[0]["note"]
    assert client.buy_reviews == []


def test_dust_cleanup_formats_six_decimals(monkeypatch):
    """Real dust sweeps go out at Robinhood's 6dp format — the old path sent
    8dp strings like '0.00120000'.

    The sweep now keys off the newest DECIDED session: it refuses to sell an
    in-basket name (a sub-$1 slice of a high-priced name is a voted hold) and
    refuses to sell anything bought since the previous close (PDT). So the
    fixture pins a newest basket that does NOT hold MSFT and ages the seeded club
    buy into a prior cycle — otherwise this is testing a guard, not the format.
    """
    mid = _member("dusty@example.com")
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id, member_id, symbol, side, type, dollar_amount, "
            "ref_id, status, created_at) VALUES(1, ?, 'MSFT', 'buy', 'market', 1.0, 'r2', "
            "'placed', strftime('%s','now'))", (mid,))
    _backdate(1, mid)
    _closed_session(datetime.date(2026, 12, 26), mid, ["AAPL"])   # MSFT is off-basket

    seen = {}

    class DustClient:
        def get_quotes(self, symbols):
            return {"data": {"results": [{"quote": {"symbol": "MSFT", "last_trade_price": "500.00"}}]}}

        def review_market_sell(self, acct, symbol, qstr):
            seen["qstr"] = qstr
            return {"data": {"symbol": symbol, "order_checks": {}}}

    monkeypatch.setattr(vault, "ensure_client", lambda m: DustClient())
    monkeypatch.setattr(executor.positions, "live_positions",
                        lambda m: [{"symbol": "MSFT", "quantity": 0.0012}])

    out = executor.dust_cleanup(mid, dry_run=True)
    assert out["ok"] is True
    assert seen["qstr"] == "0.001200"
    assert [o for o in out["orders"] if o["symbol"] == "MSFT"][0]["status"] == "reviewed"


def test_rebalance_sell_leg_skips_residue(monkeypatch):
    mid = _member("selleg-residue@x.com")
    # club ledger says the member holds a 1e-6 ALL crumb (Charlie's case):
    # a prior placed buy whose est qty is the crumb.
    sid_prev = 97001
    executor._record(sid_prev, mid, "ALL", 1.0, "crumb-ref", "placed",
                     place={"_concord_est_qty": 0.000001})
    _backdate(sid_prev, mid)
    # today's cycle drops ALL from the basket → full exit path
    closed = _closed_session(datetime.date(2026, 12, 27), mid, ["MSFT"])
    sid = closed["id"]
    client = FakeClient(quotes={"MSFT": 500.0, "ALL": 254.0},
                        positions=[("ALL", 0.000001)], buying_power=100.0)
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)

    # never reviewed, never placed
    assert client.trades_for("review_sell", f"ACC{mid}", "ALL") == []
    assert client.trades_for("place_sell", f"ACC{mid}", "ALL") == []
    rows = _exec_rows(sid, mid, "ALL", "sell")
    assert rows and rows[-1]["status"] == "skipped"
    assert "below broker sell minimum" in (rows[-1]["error"] or "")
    assert rows[-1]["ref_id"] == executor.ref_id_for(sid, mid, "SELL:ALL")
    # the skip is NOT treated as already-sold idempotency state
    assert (mid, "ALL", "sell") not in executor._placed_keys(sid)
    ms = [m for m in summary["members"] if m["member_id"] == mid][0]
    entry = [o for o in ms["orders"] if o["symbol"] == "ALL"][0]
    assert entry["status"] == "skipped" and entry["note"] == "unsellable residue"


def test_full_exit_of_0_00399_sells_exact_quantity(monkeypatch):
    """The minting bug itself, through the real sell leg: a live balance of
    0.00399 exits as '0.003990', not '0.003989'."""
    mid = _member("selleg-exact@x.com")
    sid_prev = 97002
    executor._record(sid_prev, mid, "ALL", 1.0, "crumb-ref2", "placed",
                     place={"_concord_est_qty": 0.00399})
    _backdate(sid_prev, mid)
    closed = _closed_session(datetime.date(2026, 12, 25), mid, ["MSFT"])
    client = FakeClient(quotes={"MSFT": 500.0, "ALL": 254.0},
                        positions=[("ALL", 0.00399)], buying_power=100.0)
    _use_client(monkeypatch, client)

    executor.run(closed, force_dry_run=True)

    sells = client.trades_for("review_sell", f"ACC{mid}", "ALL")
    assert sells, "expected the full exit to be reviewed"
    assert sells[0][3] == "0.003990", sells[0]


# ─── crumb repair: no same-day round trip, exit next session, bounded ───────

def test_repaired_position_not_sold_same_session_but_exits_next(monkeypatch):
    """The must-fix from the repair review: a same-day RE-RUN must not sell
    what the morning's repair bought (that is a PDT day trade); the NEXT
    session's sell leg exits the whole position.

    The guard is now keyed on TIME, not on the session's DUSTFIX ref: anything
    bought since the previous trading day's 16:00 ET close is unsellable today,
    whichever session bought it. So the skip note is the PDT one, and "the next
    session" has to be modelled the way it really arrives — a later trading day,
    with the repair's buy now behind the cutoff.
    """
    mid = _member("repair-lifecycle@x.com")
    closed = _closed_session(datetime.date(2027, 2, 2), mid, ["AAPL"])
    sid = closed["id"]
    fix_ref = executor.ref_id_for(sid, mid, "DUSTFIX:TSLA")
    # the morning's repair: a placed $1 top-up of off-basket TSLA
    executor._record(sid, mid, "TSLA", 1.00, fix_ref, "placed",
                     place={"_concord_est_qty": 0.004})
    client = FakeClient(quotes={"AAPL": 100.0, "TSLA": 250.0},
                        positions=[("TSLA", "0.004")])
    _use_client(monkeypatch, client)

    summary = executor.run(closed, force_dry_run=True)          # same-day re-run
    assert client.trades_for("review_sell", f"ACC{mid}", "TSLA") == []
    skip = next(o for o in _mrow(summary, mid)["orders"]
                if o["symbol"] == "TSLA" and o["side"] == "sell")
    assert skip["status"] == "skipped" and "PDT guard" in skip["note"]

    # next session = a later trading day: the repair's buy is now older than the
    # PDT cutoff, so the exit is free to run and sells the exact live quantity
    _backdate(sid, mid)
    closed2 = _closed_session(datetime.date(2027, 2, 3), mid, ["AAPL"])
    executor.run(closed2, force_dry_run=True)
    sells = client.trades_for("review_sell", f"ACC{mid}", "TSLA")
    assert len(sells) == 1 and sells[0][3] == "0.004000"


def test_repaired_position_not_dust_swept_same_session(monkeypatch):
    """A price dip that turns the repaired ~$1 position into a dust candidate
    must not sell it in the same session either.

    Same contract shift as the sell leg: the sweep's time-based PDT guard runs
    ahead of (and subsumes) the DUSTFIX-ref check, so the skip reads "bought
    today" rather than "repaired this session" — the position still exits with a
    later session, never same-day.
    """
    mid = _member("repair-dip@x.com")
    closed = _closed_session(datetime.date(2027, 2, 4), mid, ["AAPL"])
    sid = closed["id"]
    fix_ref = executor.ref_id_for(sid, mid, "DUSTFIX:TSLA")
    executor._record(sid, mid, "TSLA", 1.00, fix_ref, "placed",
                     place={"_concord_est_qty": 0.004})
    client = _CrumbClient()
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    # 0.004 sh at a dipped $240 = $0.96 ≤ $1 → dust candidate on value/qty
    monkeypatch.setattr(client, "get_quotes",
                        lambda symbols: {"data": {"results": [
                            {"quote": {"symbol": s, "last_trade_price": "240.00"}}
                            for s in symbols]}})
    monkeypatch.setattr(executor.positions, "live_positions",
                        lambda m: [{"symbol": "TSLA", "quantity": 0.004}])

    out = executor.dust_cleanup(mid, dry_run=True)
    entries = [o for o in out["orders"] if o["symbol"] == "TSLA"]
    assert entries and entries[0]["status"] == "skipped"
    assert "bought today — PDT guard" in entries[0]["note"]


def test_repair_budget_is_bounded_per_run(monkeypatch):
    """At most _REPAIR_MAX_PER_RUN top-ups per sweep; the rest wait."""
    mid = _member("repair-cap@x.com")
    syms = ["TSLA", "NVDA", "AMD", "NFLX"]
    with db() as conn:
        for i, sym in enumerate(syms):
            conn.execute(
                "INSERT INTO executions(session_id, member_id, symbol, side, type, "
                "dollar_amount, ref_id, status, created_at) VALUES(1, ?, ?, 'buy', "
                "'market', 1.0, ?, 'placed', strftime('%s','now'))",
                (mid, sym, f"cap-seed-{mid}-{i}"))
    _closed_session(datetime.date(2027, 2, 5), mid, ["AAPL"])
    client = _CrumbClient()
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    monkeypatch.setattr(client, "get_quotes",
                        lambda symbols: {"data": {"results": [
                            {"quote": {"symbol": s, "last_trade_price": "254.00"}}
                            for s in symbols]}})
    monkeypatch.setattr(executor.positions, "live_positions",
                        lambda m: [{"symbol": s, "quantity": 0.000001} for s in syms])

    out = executor.dust_cleanup(mid, dry_run=True)
    statuses = {o["symbol"]: o["status"] for o in out["orders"]}
    assert sum(1 for v in statuses.values() if v == "reviewed") == executor._REPAIR_MAX_PER_RUN
    budget_skips = [o for o in out["orders"] if "budget" in (o.get("note") or "")]
    assert len(budget_skips) == 1
