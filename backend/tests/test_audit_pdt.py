"""The dust sweep's PDT guard — the one path a revert-check found uncovered.

run()'s sell leg refuses to sell anything bought since the previous trading
day's 16:00 ET close, and dust_cleanup carries the SAME guard: a crumb whose
symbol the club bought today is a position minutes-to-hours old, and selling it
now is a same-day round trip — a Robinhood day trade, which on a sub-$25k
account is the thing the whole buy-today/sell-tomorrow repair design exists to
avoid. The guard is also what makes the $1 crumb repair safe: the repair buys
today and the exit waits for the next session.

Disabling that guard left the crumb suite green, so it is pinned here.

Run from backend/:  python -m pytest tests/test_audit_pdt.py -q
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

from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402

_MEMBERS: list[int] = []
_SESSIONS: list[int] = []


def setup_module(_):
    init_db()
    seed_universe()


def teardown_module(_):
    """The temp DB is shared with every other module, and this one seeds
    far-future sessions — drop exactly what it made."""
    with db() as conn:
        for mid in _MEMBERS:
            conn.execute("DELETE FROM executions WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM votes WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))
        for sid in _SESSIONS:
            conn.execute("DELETE FROM executions WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))


class _Client:
    """Scripted broker double: records every sell review the sweep attempts."""

    def __init__(self, quotes, positions):
        self.quotes, self.positions = quotes, positions
        self.sell_reviews: list[tuple] = []

    def get_positions(self, acct):
        return {"data": {"positions": [{"symbol": s, "type": "long", "quantity": str(q)}
                                       for s, q in self.positions]}}

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self.quotes[s])}}
            for s in symbols if s in self.quotes]}}

    def get_portfolio(self, acct):
        return {"data": {"buying_power": {"buying_power": "50.0"}, "total_value": "500.0"}}

    def review_market_sell(self, acct, symbol, qty):
        self.sell_reviews.append((symbol, qty))
        return {"data": {"order_checks": {}}}

    def place_market_sell(self, acct, symbol, qty, ref_id=None):
        raise AssertionError(f"a dry-run sweep must never place ({symbol} {qty})")


def _member(email: str) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,alloc_type,alloc_value,updated_at) "
            "VALUES(?,1,'dollar',50.0,?)", (mid, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?, 'connected')",
            (mid, f"ACC{mid}", b"enc", now, now))
    _MEMBERS.append(mid)
    return mid


def _closed_session(trade_date: datetime.date, mid: int, picks: list[str]) -> dict:
    s = voting.open_session_for(trade_date)
    _SESSIONS.append(s["id"])
    voting.set_member_picks(s["id"], mid, picks)
    return voting.close_and_select(s["id"])


def _age(mid: int, symbol: str, days: int) -> None:
    with db() as conn:
        conn.execute("UPDATE executions SET created_at=? WHERE member_id=? AND symbol=?",
                     (int(time.time()) - days * 86400, mid, symbol))


def _sweep(monkeypatch, mid: int, sid: int, client: _Client) -> dict:
    monkeypatch.setattr(vault, "ensure_client", lambda m: client)
    return executor.dust_cleanup(mid, dry_run=True, session_id=sid)


def test_dust_sweep_leaves_a_crumb_bought_today_alone(monkeypatch):
    """The club bought TSLA this morning; the fill left a sub-$1 crumb. Selling
    it in the same session's sweep is a buy-and-sell-the-same-day round trip —
    a day trade against a small cash account. It waits for the next session."""
    mid = _member("pdt-dust-today@x.com")
    executor._record(90820, mid, "TSLA", 1.00, "pdt-today-ref", "placed",
                     place={"_concord_est_qty": 0.003})          # created_at = now
    closed = _closed_session(datetime.date(2030, 7, 1), mid, ["MSFT"])  # TSLA is off-basket
    client = _Client({"TSLA": 250.0}, [("TSLA", "0.003")])

    out = _sweep(monkeypatch, mid, closed["id"], client)

    entry = next(o for o in out["orders"] if o["symbol"] == "TSLA")
    assert entry["side"] == "sell" and entry["status"] == "skipped"
    assert "PDT guard" in entry["note"], entry
    assert client.sell_reviews == [], "the broker must not even be asked"


def test_dust_sweep_takes_the_same_crumb_once_it_is_a_day_old(monkeypatch):
    """The other half, so the test above cannot pass by simply never sweeping:
    identical crumb, identical basket — only the buy's age differs — and the
    sweep sells it."""
    mid = _member("pdt-dust-aged@x.com")
    executor._record(90821, mid, "TSLA", 1.00, "pdt-aged-ref", "placed",
                     place={"_concord_est_qty": 0.003})
    _age(mid, "TSLA", days=7)
    closed = _closed_session(datetime.date(2030, 7, 2), mid, ["MSFT"])
    client = _Client({"TSLA": 250.0}, [("TSLA", "0.003")])

    out = _sweep(monkeypatch, mid, closed["id"], client)

    entry = next(o for o in out["orders"] if o["symbol"] == "TSLA")
    assert entry["status"] == "reviewed", entry
    assert client.sell_reviews == [("TSLA", "0.003000")]
