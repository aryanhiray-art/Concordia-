"""Tests for the History split: club-shared record vs personal ballots.

Contract:
- GET /api/club/history (any member): DECIDED sessions only (closed|executed),
  newest trade_date first, limit capped at 60; per session {id, trade_date,
  status, basket, voters, human_voters, orders} where orders = aggregate
  execution counts by status. NOTHING member-identifying — two different
  members get byte-identical payloads.
- GET /api/me/votes: only the CALLER's ballots as {id, trade_date, status,
  my_allocations, basket, hits}; open sessions appear for their owner with
  basket []/hits [] (nobody's votes leak pre-close — same rule as
  session_public, which only ever exposes my_picks + the aggregate tally);
  hits = the caller's symbols that made the decided basket.
- Both endpoints 401 unauthenticated.

Run from backend/:  python -m pytest tests/test_history_endpoints.py -q
(members + sessions inserted directly, per-test spoofed IPs, same rationale as
test_admin_panel.py. Trade dates 2026-06-* are unique to this module — the
suite shares one DB and vote_sessions.trade_date is UNIQUE.)
"""
import json
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module (test_admin_panel) sets, so
# this module behaves the same alone as inside the full suite (one process-wide
# app.config singleton).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.83.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, is_bot: int = 0) -> tuple[int, str]:
    """Member row + a session token (verified so nothing else gates the GETs)."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("hist-pass-11"),
             now, is_bot, now),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


def _mk_session(trade_date: str, status: str, picks: list[str] | None = None) -> int:
    """A vote_sessions row inserted directly (picks_json = the decided basket)."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,"
            "closed_at,top_n,picks_json) VALUES(?,?,?,?,?,?,?)",
            (trade_date, status, now, now + 3600,
             now if status in ("closed", "executed") else None, 3,
             json.dumps([{"symbol": s} for s in picks]) if picks is not None else None),
        )
        return cur.lastrowid


def _vote(sid: int, mid: int, allocs: list[tuple[str, float]]):
    now = int(time.time())
    with db() as conn:
        conn.executemany(
            "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
            "VALUES(?,?,?,?,?)",
            [(sid, mid, sym, w, now) for sym, w in allocs],
        )


def _exec_row(sid: int, mid: int, symbol: str, status: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,"
            "dollar_amount,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, mid, symbol, "buy", "market", 10.0, status, int(time.time())),
        )


# ─── auth ────────────────────────────────────────────────────────────────────

def test_both_endpoints_401_unauthenticated():
    assert client.get("/api/club/history", headers=_hdrs()).status_code == 401
    assert client.get("/api/me/votes", headers=_hdrs()).status_code == 401


# ─── /api/club/history: the shared fund record ───────────────────────────────

def test_club_history_shape_order_and_no_member_data():
    mid_a, tok_a = _mk_member("hist-alice@hclub.test")
    mid_b, tok_b = _mk_member("hist-bob@hclub.test")
    mid_bot, _ = _mk_member("hist-bot@hclub.test", is_bot=1)
    s_exec = _mk_session("2026-06-01", "executed", picks=["AAPL", "MSFT"])
    s_closed = _mk_session("2026-06-02", "closed", picks=["NVDA"])
    s_open = _mk_session("2026-06-03", "open")
    _vote(s_exec, mid_a, [("AAPL", 60), ("XOM", 40)])
    _vote(s_exec, mid_bot, [("MSFT", 100)])
    _vote(s_open, mid_a, [("NVDA", 100)])
    _exec_row(s_exec, mid_a, "AAPL", "placed")
    _exec_row(s_exec, mid_b, "AAPL", "placed")
    _exec_row(s_exec, mid_b, "MSFT", "error")

    # limit=60 (the cap): other modules seed decided sessions with later trade
    # dates into the shared DB, and this module's must still be in the window
    r = client.get("/api/club/history?limit=60", headers=_hdrs(tok_a))
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    by_id = {s["id"]: s for s in sessions}

    # decided sessions only — the open ballot is not part of the record
    assert s_exec in by_id and s_closed in by_id
    assert s_open not in by_id
    # newest trade_date first
    ids = [s["id"] for s in sessions]
    assert ids.index(s_closed) < ids.index(s_exec)

    ex = by_id[s_exec]
    assert set(ex) == {"id", "trade_date", "status", "status_display", "basket",
                       "voters", "human_voters", "orders"}
    assert ex["trade_date"] == "2026-06-01" and ex["status"] == "executed"
    assert ex["basket"] == ["AAPL", "MSFT"]          # the DECIDED picks, in order
    assert ex["voters"] == 2 and ex["human_voters"] == 1
    assert ex["orders"] == {"placed": 2, "error": 1}  # aggregate — no member rows
    cl = by_id[s_closed]
    assert cl["basket"] == ["NVDA"] and cl["orders"] == {}
    assert cl["voters"] == 0 and cl["human_voters"] == 0

    # NOTHING member-identifying anywhere in the payload
    assert "hclub.test" not in r.text                # no emails
    assert "hist-alice" not in r.text and "hist-bob" not in r.text  # no names
    assert "member_id" not in r.text

    # the record is shared: two different members get byte-identical payloads
    r_b = client.get("/api/club/history?limit=60", headers=_hdrs(tok_b))
    assert r_b.status_code == 200
    assert r_b.content == r.content


def test_club_history_limit_is_clamped():
    _, tok = _mk_member("hist-limit@hclub.test")
    _mk_session("2026-06-04", "closed", picks=["KO"])
    _mk_session("2026-06-05", "closed", picks=["PEP"])
    one = client.get("/api/club/history?limit=1", headers=_hdrs(tok)).json()["sessions"]
    assert len(one) == 1
    big = client.get("/api/club/history?limit=500", headers=_hdrs(tok))
    assert big.status_code == 200
    assert len(big.json()["sessions"]) <= 60


# ─── /api/me/votes: the caller's personal ballots ────────────────────────────

def test_me_votes_only_caller_rows_and_hits():
    mid_a, tok_a = _mk_member("mv-alice@hclub.test")
    mid_b, tok_b = _mk_member("mv-bob@hclub.test")
    sid = _mk_session("2026-06-06", "executed", picks=["AAPL", "MSFT"])
    sid_b_only = _mk_session("2026-06-07", "closed", picks=["ZBRA"])
    _vote(sid, mid_a, [("AAPL", 60), ("XOM", 40)])
    _vote(sid, mid_b, [("MSFT", 70), ("ZBRA", 30)])
    _vote(sid_b_only, mid_b, [("ZBRA", 100)])

    r_a = client.get("/api/me/votes", headers=_hdrs(tok_a))
    assert r_a.status_code == 200
    a_sessions = {s["id"]: s for s in r_a.json()["sessions"]}
    assert set(a_sessions) == {sid}                  # only sessions A voted in
    mine = a_sessions[sid]
    assert set(mine) == {"id", "trade_date", "status", "status_display",
                         "my_allocations", "basket", "hits"}
    assert mine["trade_date"] == "2026-06-06" and mine["status"] == "executed"
    assert mine["my_allocations"] == [{"symbol": "AAPL", "weight": 60.0},
                                      {"symbol": "XOM", "weight": 40.0}]
    assert mine["basket"] == ["AAPL", "MSFT"]        # what the club decided
    assert mine["hits"] == ["AAPL"]                  # XOM missed, MSFT wasn't mine
    # B's ballot must not appear ANYWHERE in A's payload (ZBRA = B's unique pick;
    # basket ZBRA in sid_b_only never reaches A because A didn't vote there)
    assert "ZBRA" not in r_a.text
    assert "mv-bob" not in r_a.text and "hclub.test" not in r_a.text

    r_b = client.get("/api/me/votes", headers=_hdrs(tok_b))
    b_sessions = {s["id"]: s for s in r_b.json()["sessions"]}
    assert set(b_sessions) == {sid, sid_b_only}
    assert b_sessions[sid]["my_allocations"] == [{"symbol": "MSFT", "weight": 70.0},
                                                 {"symbol": "ZBRA", "weight": 30.0}]
    assert b_sessions[sid]["hits"] == ["MSFT"]
    assert b_sessions[sid_b_only]["hits"] == ["ZBRA"]
    # newest first
    b_ids = [s["id"] for s in r_b.json()["sessions"]]
    assert b_ids.index(sid_b_only) < b_ids.index(sid)
    assert "XOM" not in r_b.text                     # A's miss never reaches B


def test_open_session_ballot_visible_to_owner_with_empty_basket():
    mid_a, tok_a = _mk_member("open-alice@hclub.test")
    _, tok_b = _mk_member("open-bob@hclub.test")
    # picks_json deliberately set on an OPEN session: the basket must STILL be
    # hidden pre-close (status gates it, not the row's contents)
    sid = _mk_session("2026-06-08", "open", picks=["NVDA"])
    _vote(sid, mid_a, [("NVDA", 55), ("AMD", 45)])

    a_sessions = {s["id"]: s for s in
                  client.get("/api/me/votes", headers=_hdrs(tok_a)).json()["sessions"]}
    mine = a_sessions[sid]                           # the owner's own ballot IS there
    assert mine["status"] == "open"
    assert mine["my_allocations"] == [{"symbol": "NVDA", "weight": 55.0},
                                      {"symbol": "AMD", "weight": 45.0}]
    assert mine["basket"] == [] and mine["hits"] == []   # nothing decided pre-close

    # ...and the open ballot never leaks to anyone else, in either endpoint
    r_b = client.get("/api/me/votes", headers=_hdrs(tok_b))
    assert sid not in {s["id"] for s in r_b.json()["sessions"]}
    hist = client.get("/api/club/history", headers=_hdrs(tok_b))
    assert sid not in {s["id"] for s in hist.json()["sessions"]}


# ─── status_display: a stale-closed row's cycle is over ──────────────────────

def test_status_display_marks_stale_closed_as_executed():
    """A 'closed' session that is no longer the LATEST decided one can never run
    its buy leg (the executor only processes the latest cycle) — History shows it
    as 'executed' via status_display, while the raw status (trading data) stays
    'closed' and the vote_sessions row is never mutated."""
    mid, tok = _mk_member("sd-alice@hclub.test")
    # 2030-* dates sort after every date other modules seed (real 2026 dates via
    # close_and_select, 2026-06-* above), so "latest decided" is deterministic
    # here. picks=None keeps these out of performance.decided_cycles entirely.
    s_stale = _mk_session("2030-06-01", "closed")
    s_exec = _mk_session("2030-06-02", "executed")
    s_latest = _mk_session("2030-06-03", "closed")
    _vote(s_stale, mid, [("AAPL", 100)])
    _vote(s_latest, mid, [("MSFT", 100)])

    r = client.get("/api/club/history?limit=60", headers=_hdrs(tok))
    assert r.status_code == 200
    by_id = {s["id"]: s for s in r.json()["sessions"]}
    # stale closed → displayed executed; raw status kept in the payload
    assert by_id[s_stale]["status"] == "closed"
    assert by_id[s_stale]["status_display"] == "executed"
    # the latest decided session IS the live cycle — stays closed
    assert by_id[s_latest]["status"] == "closed"
    assert by_id[s_latest]["status_display"] == "closed"
    # executed rows display as themselves
    assert by_id[s_exec]["status"] == by_id[s_exec]["status_display"] == "executed"
    # derivation only — the trading rows are untouched
    with db() as conn:
        raw = conn.execute("SELECT status FROM vote_sessions WHERE id IN (?,?,?) "
                           "ORDER BY id", (s_stale, s_exec, s_latest)).fetchall()
    assert [x["status"] for x in raw] == ["closed", "executed", "closed"]

    # the personal history applies the SAME derivation
    mine = {s["id"]: s for s in
            client.get("/api/me/votes", headers=_hdrs(tok)).json()["sessions"]}
    assert mine[s_stale]["status"] == "closed"
    assert mine[s_stale]["status_display"] == "executed"
    assert mine[s_latest]["status_display"] == "closed"


# ─── status_display: an abandoned open ballot is 'expired', not 'open' ────────

def test_stale_open_session_displays_expired_and_genuine_open_stays_open():
    """Prod carries vote_sessions id=4 (2026-06-22, status='open', closes_at
    2026-06-22 12:30) — a ballot abandoned mid-cycle over a month ago. It must
    never present as an open ballot ("results after close" for a cycle that will
    never close); it displays as 'expired', with no basket. A ballot whose close
    time is still ahead is the real thing and is untouched.

    Trade dates are back-dated and both rows are removed at the end so this test
    can't leave a stray 'open' session for the shared-DB modules that follow.
    """
    mid, tok = _mk_member("sd-stale@hclub.test")
    now = int(time.time())
    with db() as conn:
        stale = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES('2026-06-25','open',?,?,3)", (now - 400_000, now - 300_000),
        ).lastrowid
        live = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES('2026-06-26','open',?,?,3)", (now, now + 3600),
        ).lastrowid
    _vote(stale, mid, [("AAPL", 100)])
    _vote(live, mid, [("MSFT", 100)])
    try:
        mine = {s["id"]: s for s in
                client.get("/api/me/votes", headers=_hdrs(tok)).json()["sessions"]}
        # the member DID vote in the dead ballot — it stays visible, told honestly
        assert mine[stale]["status"] == "open"
        assert mine[stale]["status_display"] == "expired"
        assert mine[stale]["basket"] == [] and mine[stale]["hits"] == []
        assert [a["symbol"] for a in mine[stale]["my_allocations"]] == ["AAPL"]
        # a ballot that hasn't hit its close time yet is the genuine open one
        assert mine[live]["status"] == mine[live]["status_display"] == "open"

        # derivation only — the trading rows keep saying 'open'
        with db() as conn:
            raw = conn.execute("SELECT status FROM vote_sessions WHERE id IN (?,?) "
                               "ORDER BY id", (stale, live)).fetchall()
        assert [x["status"] for x in raw] == ["open", "open"]

        # club history never lists undecided sessions at all
        hist = client.get("/api/club/history?limit=60", headers=_hdrs(tok)).json()["sessions"]
        assert {stale, live}.isdisjoint({s["id"] for s in hist})
    finally:
        with db() as conn:
            conn.execute("DELETE FROM votes WHERE session_id IN (?,?)", (stale, live))
            conn.execute("DELETE FROM vote_sessions WHERE id IN (?,?)", (stale, live))


def test_is_abandoned_open_rule_matches_the_admin_console():
    """One staleness rule, shared: the console's _current_open_session drops the
    same rows the history views mark 'expired' (a third copy is how they drift)."""
    from app.services import adminview
    from app.services.voting import is_abandoned_open

    now = int(time.time())
    assert is_abandoned_open({"status": "open", "closes_at": now - 90_000}) is True
    assert is_abandoned_open({"status": "open", "closes_at": now - 3_600}) is False
    assert is_abandoned_open({"status": "open", "closes_at": now + 3_600}) is False
    # only OPEN rows can be abandoned — a decided cycle is just history
    assert is_abandoned_open({"status": "closed", "closes_at": now - 90_000}) is False
    assert is_abandoned_open({"status": "executed", "closes_at": now - 90_000}) is False
    # a row with no close time recorded is not evidence of abandonment
    assert is_abandoned_open({"status": "open", "closes_at": None}) is False

    # and the console honours it: the newest open row being abandoned means
    # there is no current ballot to judge quorum on, not "quorum met yesterday".
    with db() as conn:
        sid = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES('2099-01-01','open',?,?,3)", (now - 400_000, now - 300_000),
        ).lastrowid
    try:
        assert adminview._current_open_session() is None
    finally:
        with db() as conn:
            conn.execute("DELETE FROM vote_sessions WHERE id=?", (sid,))


def test_demo_roll_session_rolls_an_expired_ballot(monkeypatch):
    """Regression: the roll passed a STRING to open_session_for, which calls
    .isoformat() internally -> AttributeError -> 500 on every ballot read on
    the demo. Prove the roll now closes an expired ballot and opens a fresh
    one with a future close."""
    import time as _time

    from app.config import settings as _settings
    from app.services import voting as _voting

    # Settings is a frozen dataclass — go around the freeze, restore after.
    # Everything runs inside the try so a failing assertion can NEVER leak
    # demo_mode=True (or the parked rows) into later modules — a leak here is
    # what used to knock over test_verify_email/test_vault_reauth/etc. This test
    # must also hold at any wall-clock time: the roll now always opens a ballot
    # with a genuinely future close, so the assertion below can't flip after
    # 08:30 ET (the old flake).
    object.__setattr__(_settings, "demo_mode", True)
    monkeypatch  # unused; kept for signature symmetry
    try:
        with db() as conn:
            # Other tests in this module leave their own OPEN sessions with future
            # closes; the roll rightly ignores an expired row when a live ballot
            # exists, so park them for the duration of this test.
            conn.execute("UPDATE vote_sessions SET status='parked' WHERE status='open'")
            conn.execute(
                "INSERT INTO vote_sessions(trade_date,status,closes_at,top_n) "
                "VALUES('2026-01-05','open',?,20)", (int(_time.time()) - 86400,))
        _voting.demo_roll_session()
        fresh = _voting.latest_session("open")
        assert fresh is not None
        assert fresh["closes_at"] > _time.time(), "the rolled ballot must have a future close"
        with db() as conn:
            old = conn.execute(
                "SELECT status FROM vote_sessions WHERE trade_date='2026-01-05'").fetchone()
        assert old["status"] != "open", "the expired ballot was closed, not left dangling"
    finally:
        object.__setattr__(_settings, "demo_mode", False)
        with db() as conn:
            conn.execute("DELETE FROM vote_sessions WHERE trade_date='2026-01-05' "
                         "OR (status='open' AND closes_at > strftime('%s','now'))")
            conn.execute("UPDATE vote_sessions SET status='open' WHERE status='parked'")
