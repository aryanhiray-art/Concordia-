"""Tests for the cached per-member account snapshot (services/accountsnap).

Why it exists: GET /api/me/account and GET /api/me/holdings each did LIVE
Robinhood reads on EVERY page visit. They now read a snapshot row that an
interval job (and a background revalidate) keeps warm.

Contract:
- GET /api/me/account  -> {allocation, pnl, power, as_of, stale, live}
- GET /api/me/holdings -> {holdings, as_of, stale}
- POST /api/me/account/refresh -> the /me/account shape, forced live, 1 per 30s
- PUT /api/me/allocation -> the /me/account shape, ALWAYS freshly refreshed
- The first read builds the snapshot (one balance read + ONE holdings read — the
  club-P&L reuses the holdings list); every later read inside FRESH_SECS costs
  the broker NOTHING. That counter assertion is the core regression test.
- A broker exception keeps the previous GOOD payload, flips ok=0, and leaves
  refreshed_at at the last successful read so `as_of` never lies.
- A member with no Robinhood connection gets a valid empty-ish payload, no 500.

SAFETY: this cache is display-only. The trading path (alloc.member_balance,
alloc.rebalance_budget, positions.holdings_with_value, executor/power/reputation)
still reads live and is untouched by these tests.

Run from backend/:  python -m pytest tests/test_account_snapshot.py -q
(members inserted directly + per-test spoofed IPs, same rationale as
test_admin_panel.py; the two live reads are monkeypatched with call counters.)
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# This module now sorts FIRST alphabetically, so ITS imports win the process-wide
# app.config singleton — it must set the SAME env the previously-first modules
# (test_admin_panel / test_audit_fixes) set, or the whole suite's TOP_N /
# EXECUTION_MODE shift underneath them.
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import accountsnap, alloc, positions  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]

_BALANCE = {"total_value": 1000.0, "cash": 250.0, "buying_power": 250.0}
_HOLDINGS = [{"symbol": "AAPL", "quantity": 2.0, "value": 400.0, "pnl": 40.0},
             {"symbol": "MSFT", "quantity": 1.0, "value": 300.0, "pnl": -10.0}]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.82.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, connected: bool = True, opted_in: int = 1,
               alloc_type: str = "dollar", alloc_value: float = 100.0) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("snap-pass-11"), now, 0, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,?,0,0,?,?,?,?,?)",
            (mid, opted_in, settings.max_orders, settings.voting_power_base,
             alloc_type, alloc_value, now),
        )
        if connected:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,status,"
                "connected_at) VALUES(?,?,?,?,?)", (mid, f"ACC{mid}", 1, "connected", now))
    return mid, make_session(mid, 1)


class _Broker:
    """Stand-in for the two live Robinhood reads a snapshot costs, with counters."""

    def __init__(self):
        self.balance = dict(_BALANCE)
        self.holdings = [dict(h) for h in _HOLDINGS]
        self.balance_calls = 0
        self.holdings_calls = 0
        self.boom = False

    def member_balance(self, member_id: int):
        self.balance_calls += 1
        if self.boom:
            raise RuntimeError("robinhood is down")
        return dict(self.balance)

    def holdings_with_value(self, member_id: int):
        self.holdings_calls += 1
        if self.boom:
            raise RuntimeError("robinhood is down")
        return [dict(h) for h in self.holdings]

    @property
    def calls(self) -> int:
        return self.balance_calls + self.holdings_calls


def _wire_broker(monkeypatch) -> _Broker:
    b = _Broker()
    monkeypatch.setattr(alloc, "member_balance", b.member_balance)
    monkeypatch.setattr(positions, "holdings_with_value", b.holdings_with_value)
    return b


def _backdate(member_id: int, seconds: int) -> None:
    with db() as conn:
        conn.execute("UPDATE account_snapshots SET refreshed_at=refreshed_at-? WHERE member_id=?",
                     (seconds, member_id))


def _freeze_background(member_id: int) -> None:
    """Suppress the background revalidate so a test is deterministic (the retry
    gap already blocks a kick this soon after an attempt)."""
    accountsnap._last_attempt[member_id] = time.time()


def _wait_for_refresh(member_id: int, was: int, timeout: float = 10.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = accountsnap.read(member_id)
        if row and row["refreshed_at"] != was:
            return row["refreshed_at"]
        time.sleep(0.05)
    raise AssertionError("background refresh never landed")


# ─── the core regression test: reads must stop hitting the broker ────────────

def test_first_read_builds_snapshot_and_later_reads_cost_no_broker_calls(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-cache@aclub.test")

    r = client.get("/api/me/account", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    # ONE balance read + ONE holdings read — club_pnl reused the holdings list
    # instead of re-reading positions+quotes (it would otherwise be 2).
    assert (b.balance_calls, b.holdings_calls) == (1, 1)
    assert body["live"] is True                      # this response did the read
    assert body["allocation"]["balance"] == _BALANCE
    assert body["allocation"]["resolved_usd"] == 100.0
    assert body["pnl"] == {"invested": 670.0, "value": 700.0, "profit": 30.0,
                           "profit_pct": 4.48, "positions": 2}
    first_as_of = body["as_of"]

    # the snapshot row is persisted
    with db() as conn:
        row = conn.execute("SELECT ok FROM account_snapshots WHERE member_id=?", (mid,)).fetchone()
    assert row is not None and row["ok"] == 1

    # every subsequent read inside FRESH_SECS is a pure DB read
    for _ in range(3):
        again = client.get("/api/me/account", headers=_hdrs(tok)).json()
        assert again["live"] is False and again["as_of"] == first_as_of
        assert again["pnl"] == body["pnl"]
    h = client.get("/api/me/holdings", headers=_hdrs(tok))
    assert h.status_code == 200
    # /me/holdings annotates each row with `club` (this member has no club
    # executions, so every position is personal) — the snapshot itself is
    # unchanged, the flag is added at the route.
    assert h.json()["holdings"] == [{**row, "club": False} for row in _HOLDINGS]
    assert h.json()["as_of"] == first_as_of
    assert b.calls == 2, "opening Overview/Account again must not touch Robinhood"


def test_response_shapes_are_the_agreed_contract(monkeypatch):
    _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-shape@aclub.test")

    acct = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert set(acct) == {"allocation", "pnl", "power", "as_of", "stale", "live"}
    assert isinstance(acct["as_of"], int) and acct["stale"] is False
    assert set(acct["power"]) == {"power", "delta", "as_of_week", "history"}

    hold = client.get("/api/me/holdings", headers=_hdrs(tok)).json()
    assert set(hold) == {"holdings", "as_of", "stale"}

    # older than STALE_SECS → still served, but flagged so the UI can say so
    _backdate(mid, accountsnap.STALE_SECS + 60)
    _freeze_background(mid)
    stale = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert stale["stale"] is True and stale["live"] is False
    assert stale["allocation"]["balance"] == _BALANCE      # old data still shown
    assert client.get("/api/me/holdings", headers=_hdrs(tok)).json()["stale"] is True


def test_aged_snapshot_is_served_immediately_then_revalidated_in_background(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-swr@aclub.test")
    was = client.get("/api/me/account", headers=_hdrs(tok)).json()["as_of"]
    assert b.calls == 2

    _backdate(mid, accountsnap.FRESH_SECS + 60)
    accountsnap._last_attempt.pop(mid, None)          # allow the kick
    b.balance["total_value"] = 4000.0                 # the account moved meanwhile

    served = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert served["live"] is False                     # answered from cache, no wait
    assert served["allocation"]["balance"]["total_value"] == 1000.0

    _wait_for_refresh(mid, was - accountsnap.FRESH_SECS - 60)
    assert b.calls == 4                                # exactly one extra build
    fresh = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert fresh["allocation"]["balance"]["total_value"] == 4000.0


# ─── failure handling ────────────────────────────────────────────────────────

def test_broker_failure_keeps_last_good_payload_and_marks_not_ok(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-boom@aclub.test")
    good = client.get("/api/me/account", headers=_hdrs(tok)).json()

    b.boom = True
    row = accountsnap.refresh(mid)
    assert row["ok"] is False and "robinhood is down" in row["error"]
    assert row["payload"]["pnl"] == good["pnl"]               # good numbers kept
    assert row["holdings"] == _HOLDINGS
    assert row["refreshed_at"] == good["as_of"], "as_of must stay at the last SUCCESS"

    _freeze_background(mid)
    still = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert still["allocation"]["balance"] == _BALANCE
    assert still["as_of"] == good["as_of"]

    # recovery: a later successful refresh clears the flag and moves as_of on
    b.boom = False
    time.sleep(1.1)                                          # as_of has 1s resolution
    ok_row = accountsnap.refresh(mid)
    assert ok_row["ok"] is True and ok_row["error"] is None
    assert ok_row["refreshed_at"] > good["as_of"]


def test_member_without_robinhood_gets_valid_empty_payload():
    mid, tok = _mk_member("snap-noconnect@aclub.test", connected=False)

    r = client.get("/api/me/account", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["allocation"]["balance"] is None
    assert body["allocation"]["resolved_usd"] == 0.0
    assert body["pnl"]["positions"] == 0 and body["pnl"]["value"] == 0.0
    assert isinstance(body["as_of"], int) and body["stale"] is False

    h = client.get("/api/me/holdings", headers=_hdrs(tok))
    assert h.status_code == 200 and h.json()["holdings"] == []


# ─── writes that must not serve a stale snapshot ─────────────────────────────

def test_put_allocation_returns_a_freshly_refreshed_snapshot(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-alloc@aclub.test")
    primed = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert primed["allocation"]["resolved_usd"] == 100.0

    b.balance["total_value"] = 2000.0        # account grew since the snapshot
    r = client.put("/api/me/allocation", headers=_hdrs(tok),
                   json={"type": "percent", "value": 25})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"allocation", "pnl", "power", "as_of", "stale", "live"}
    assert body["live"] is True
    assert body["allocation"] == {"type": "percent", "value": 25.0,
                                  "resolved_usd": 500.0, "compounding": False,
                                  "balance": b.balance}
    assert body["as_of"] > primed["as_of"] or b.calls > 2

    # and the cache now serves the new stake without another broker call
    calls = b.calls
    cached = client.get("/api/me/account", headers=_hdrs(tok)).json()
    assert cached["allocation"]["resolved_usd"] == 500.0
    assert cached["live"] is False and b.calls == calls


def test_forced_refresh_endpoint_works_then_rate_limits(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-force@aclub.test")
    client.get("/api/me/account", headers=_hdrs(tok))
    ratelimit.clear(f"acctrefresh:{mid}")

    b.balance["total_value"] = 3000.0
    r = client.post("/api/me/account/refresh", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"allocation", "pnl", "power", "as_of", "stale", "live"}
    assert body["live"] is True
    assert body["allocation"]["balance"]["total_value"] == 3000.0

    calls = b.calls
    r2 = client.post("/api/me/account/refresh", headers=_hdrs(tok))
    assert r2.status_code == 429
    assert "refresh" in r2.json()["detail"]
    assert b.calls == calls, "a rate-limited refresh must not reach the broker"


# ─── invalidation ────────────────────────────────────────────────────────────

def test_disconnect_invalidates_the_snapshot(monkeypatch):
    _wire_broker(monkeypatch)
    mid, tok = _mk_member("snap-disconnect@aclub.test")
    client.get("/api/me/account", headers=_hdrs(tok))
    assert accountsnap.read(mid) is not None

    assert client.post("/api/oauth/robinhood/disconnect", headers=_hdrs(tok)).status_code == 200
    assert accountsnap.read(mid) is None, "a disconnected account must not keep serving"


# ─── the interval refresher ──────────────────────────────────────────────────

def test_refresh_all_connected_covers_opted_in_connected_members_only(monkeypatch):
    b = _wire_broker(monkeypatch)
    live_mid, _ = _mk_member("snap-job-in@aclub.test", opted_in=1)
    out_mid, _ = _mk_member("snap-job-out@aclub.test", opted_in=0)
    off_mid, _ = _mk_member("snap-job-off@aclub.test", connected=False, opted_in=1)

    ids = accountsnap.connected_member_ids()
    assert live_mid in ids and out_mid not in ids and off_mid not in ids

    res = accountsnap.refresh_all_connected(sleep_s=0)
    assert res["members"] == res["ok"] and res["failed"] == []
    assert accountsnap.read(live_mid)["ok"] is True
    assert accountsnap.read(out_mid) is None
    assert b.calls >= 2
