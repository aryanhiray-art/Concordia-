"""Tests for the stock-data endpoints behind the search page + detail view.

Contract:
- GET /api/stocks/search?q= -> {results: [{symbol, name}]} from the votable
  universe (same matcher as /api/universe), member-only.
- GET /api/stocks/{symbol} -> {symbol, name, quote{price,prev_close,change_pct},
  day{d,open,high,low,volume}, range{1M,3M,1Y: [{d,c}]}, fundamentals{...},
  in_universe, as_of}.
  Garbage/unknown symbols -> clean 404. Universe symbols serve even with the
  broker unreachable (nulls, empty ranges) — search must never 500.
- Both 401 unauthenticated.

Run from backend/:  python -m pytest tests/test_stocks_api.py -q
(members inserted directly + per-test spoofed IPs, same rationale as
test_admin_panel.py.)
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module sets, so this module behaves
# the same alone as inside the full suite (one process-wide config singleton).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import stocks as stocks_svc  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()
    with db() as conn:
        # Upsert, not INSERT OR IGNORE: the DB is shared process-wide, and other
        # modules seed placeholder universe rows whose name is just the symbol
        # (test_vote_reminders._seed_universe). OR IGNORE would leave that
        # placeholder in place and the name assertions below would read 'AAPL'
        # instead of 'Apple Inc.' whenever that module imported first. This
        # mirrors app.seed.seed_universe, which upserts for the same reason.
        conn.execute(
            "INSERT INTO universe(symbol,name,active,sort_order) "
            "VALUES('AAPL','Apple Inc.',1,1),('AMD','Advanced Micro Devices',1,2) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,"
            "active=excluded.active,sort_order=excluded.sort_order"
        )


def setup_function(_):
    # each test decides what the "broker" returns — never inherit another
    # test's cached quote/fundamentals/closes
    stocks_svc._cache.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.87.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("stock-pass-11"),
             now, 0, now),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


class _FakeClient:
    """The three read tools the detail payload touches, in broker shape."""

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"symbol": s, "last_trade_price": "200.00", "previous_close": "190.00"}
            for s in symbols
        ]}}

    def get_equity_fundamentals(self, symbols):
        return {"data": {"results": [
            {"symbol": s, "market_cap": "3000000000000", "pe_ratio": "31.5",
             "high_52_weeks": "240.1", "low_52_weeks": "160.2",
             "sector": "Technology"}
            for s in symbols
        ]}}

    def get_equity_historicals(self, symbols, start_time, interval="day"):
        older = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 9 * 86400))
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
        return {"data": {"results": [
            {"symbol": s, "historicals": [
                {"begins_at": f"{older}T00:00:00Z", "close_price": "180.00",
                 "open_price": "178.00", "high_price": "181.00",
                 "low_price": "177.00", "volume": 1_000_000},
                {"begins_at": f"{day}T00:00:00Z", "close_price": "195.50",
                 "open_price": "193.00", "high_price": "197.25",
                 "low_price": "192.10", "volume": 41_000_000},
            ]}
            for s in symbols
        ]}}


def _wire_broker(monkeypatch, client_obj):
    monkeypatch.setattr(stocks_svc, "_client", lambda: client_obj)


# ─── the contract ────────────────────────────────────────────────────────────

def test_both_endpoints_401_unauthenticated():
    assert client.get("/api/stocks/search?q=aa", headers=_hdrs()).status_code == 401
    assert client.get("/api/stocks/AAPL", headers=_hdrs()).status_code == 401


def test_search_matches_universe_by_symbol_and_name(monkeypatch):
    _mid, tok = _mk_member("stock-search@aclub.test")
    r = client.get("/api/stocks/search?q=aap", headers=_hdrs(tok))
    assert r.status_code == 200
    hits = r.json()["results"]
    assert {"symbol": "AAPL", "name": "Apple Inc."} in hits
    # name substring finds it too
    hits2 = client.get("/api/stocks/search?q=micro", headers=_hdrs(tok)).json()["results"]
    assert any(h["symbol"] == "AMD" for h in hits2)


def test_detail_shape_from_a_mocked_broker(monkeypatch):
    _mid, tok = _mk_member("stock-detail@aclub.test")
    _wire_broker(monkeypatch, _FakeClient())
    r = client.get("/api/stocks/AAPL", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL" and body["name"] == "Apple Inc."
    assert body["in_universe"] is True and isinstance(body["as_of"], int)
    assert body["quote"] == {"price": 200.0, "prev_close": 190.0, "change_pct": 5.26}
    assert body["fundamentals"]["market_cap"] == 3_000_000_000_000
    assert body["fundamentals"]["pe_ratio"] == 31.5
    assert body["fundamentals"]["high_52w"] == 240.1
    assert body["fundamentals"]["sector"] == "Technology"
    assert body["fundamentals"]["dividend_yield"] is None   # absent -> null, not missing
    for rng in ("1M", "3M", "1Y"):
        assert body["range"][rng] and body["range"][rng][-1]["c"] == 195.5


def test_day_range_comes_off_the_latest_bar(monkeypatch):
    """The quote endpoint carries no intraday high/low, so the day strip reads
    the LAST daily bar of the history call we already make — never an older one,
    and never an extra broker round-trip."""
    _mid, tok = _mk_member("stock-day@aclub.test")
    _wire_broker(monkeypatch, _FakeClient())
    day = client.get("/api/stocks/AAPL", headers=_hdrs(tok)).json()["day"]
    assert day["d"] == time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
    assert day["open"] == 193.0 and day["high"] == 197.25 and day["low"] == 192.1
    assert day["volume"] == 41_000_000


def test_day_range_is_null_when_the_broker_omits_ohlc(monkeypatch):
    """Close-only bars (an older/partial broker payload) must still serve — the
    day fields come back null so the UI hides the strip, never 0.0."""
    _mid, tok = _mk_member("stock-day-null@aclub.test")

    class CloseOnly(_FakeClient):
        def get_equity_historicals(self, symbols, start_time, interval="day"):
            d = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 2 * 86400))
            return {"data": {"results": [
                {"symbol": s, "historicals": [{"begins_at": d, "close_price": "12.5"}]}
                for s in symbols]}}

    _wire_broker(monkeypatch, CloseOnly())
    day = client.get("/api/stocks/AAPL", headers=_hdrs(tok)).json()["day"]
    assert day["high"] is None and day["low"] is None and day["volume"] is None
    assert day["d"] == time.strftime("%Y-%m-%d", time.gmtime(time.time() - 2 * 86400))


def test_detail_is_cached_second_hit_costs_no_broker_calls(monkeypatch):
    _mid, tok = _mk_member("stock-cache@aclub.test")
    calls = {"n": 0}

    class Counting(_FakeClient):
        def get_quotes(self, symbols):
            calls["n"] += 1
            return super().get_quotes(symbols)

    _wire_broker(monkeypatch, Counting())
    assert client.get("/api/stocks/AAPL", headers=_hdrs(tok)).status_code == 200
    assert client.get("/api/stocks/AAPL", headers=_hdrs(tok)).status_code == 200
    assert calls["n"] == 1, "second view inside the TTL must be served from cache"


def test_garbage_symbol_404s_and_universe_survives_broker_down(monkeypatch):
    _mid, tok = _mk_member("stock-404@aclub.test")
    _wire_broker(monkeypatch, None)   # nobody connected / broker unreachable
    assert client.get("/api/stocks/NOPE123", headers=_hdrs(tok)).status_code == 404
    assert client.get("/api/stocks/ZZZZZ", headers=_hdrs(tok)).status_code == 404
    # a universe symbol still serves: nulls + empty ranges, never a 500
    r = client.get("/api/stocks/AAPL", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["price"] is None
    assert body["range"] == {"1M": [], "3M": [], "1Y": []}
    assert body["day"] == {"d": None, "open": None, "high": None,
                           "low": None, "volume": None}
