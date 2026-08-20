"""Tests for the admin console's schedule strip + bot roster endpoints.

Contract:
- GET /api/admin/schedule: {cadence, today:{date,phase}, upcoming:[7 x
  {date,weekday,phase}]} — phases come from calendar_us.cycle_phase with None
  rendered as "idle"; upcoming = the next 7 TRADING days (weekends/holidays
  skipped, so under mwf every entry is 'both' or 'buy', never 'idle').
- GET /api/admin/bots: {bots:[{id, display_name, voting_power, accuracy,
  last_vote_at, last_picks}], llm:{reachable, detail}} — last_picks are the
  bot's NEWEST session's bot_picks, conviction-ordered; rows sort by voting
  power; the LLM probe result passes through verbatim.
- ollama.probe never raises — an unreachable endpoint (dropped Forge tunnel)
  is a {"reachable": False} payload, not a 500.
- adminview.member_rows passes through the kv-persisted broker account type
  (`acct_type:{member_id}`): value → string, empty/never-probed → null.

Run from backend/:  python -m pytest tests/test_admin_schedule_bots.py -q
(throwaway temp DB; members inserted directly + per-test spoofed IPs, same
rationale as test_admin_panel.py; the Ollama probe is monkeypatched or aimed
at a dead local port — no network.)
"""
import datetime as dt
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module pins (see test_admin_panel.py)
# so running this file alone matches the full-suite config singleton.
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.bots import ollama  # noqa: E402
from app.calendar_us import is_trading_day, today_et  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_set  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import adminview  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.82.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_admin(email: str) -> str:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,created_at) VALUES(?,?,?,?,?,?)",
            (email, email.split("@")[0], "admin", hash_password("sched-pass-11"),
             now, now),
        )
        mid = cur.lastrowid
    return make_session(mid, 1)


def _mk_human(email: str) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,created_at) VALUES(?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("sched-pass-11"),
             now, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,0,0,0,25,0,'dollar',0,?)", (mid, now))
    return mid


def _mk_bot(email: str, power: float, accuracy: float = 0.0) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,bot_slug,created_at) "
            "VALUES(?,?,'bot',1,?,?)",
            (email, email.split("@")[0], email.split("@")[0], now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,accuracy,"
            "updated_at) VALUES(?,1,0,0,0,?,'dollar',0,?,?)",
            (mid, power, accuracy, now))
    return mid


def _session(trade_date: str) -> int:
    """A finished session — status 'executed' so it can never masquerade as the
    open/closed ballot other test modules' quorum checks look for (shared DB)."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,top_n) "
            "VALUES(?,?,?,?)", (trade_date, "executed", int(time.time()), 20))
        return cur.lastrowid


# ─── GET /api/admin/schedule ─────────────────────────────────────────────────

def test_schedule_shape_and_phases_under_mwf():
    tok = _mk_admin("sched-admin@sclub.test")
    prev = (settings.cycle_cadence, settings.cycle_start_date)
    object.__setattr__(settings, "cycle_cadence", "mwf")
    object.__setattr__(settings, "cycle_start_date", "2020-01-01")
    try:
        r = client.get("/api/admin/schedule", headers=_hdrs(tok))
        assert r.status_code == 200
        j = r.json()
        assert set(j) == {"cadence", "today", "upcoming"}
        assert j["cadence"] == "mwf"

        today = today_et()
        assert j["today"]["date"] == today.isoformat()
        # Today's phase from first principles: non-trading day → "idle";
        # under mwf, Mon/Wed/Fri vote days are 'both', Tue/Thu top up.
        if not is_trading_day(today):
            assert j["today"]["phase"] == "idle"
        else:
            assert j["today"]["phase"] == ("both" if today.weekday() in (0, 2, 4) else "buy")

        assert len(j["upcoming"]) == 7
        prev_d = today
        for row in j["upcoming"]:
            assert set(row) == {"date", "weekday", "phase"}
            d = dt.date.fromisoformat(row["date"])
            assert d > prev_d, "upcoming dates must strictly ascend"
            prev_d = d
            assert is_trading_day(d), "upcoming lists trading days only"
            assert row["weekday"] == d.strftime("%a")
            # mwf never idles a trading day: every entry is a vote or top-up day
            assert row["phase"] == ("both" if d.weekday() in (0, 2, 4) else "buy")
    finally:
        object.__setattr__(settings, "cycle_cadence", prev[0])
        object.__setattr__(settings, "cycle_start_date", prev[1])


def test_schedule_renders_none_phase_as_idle():
    tok = _mk_admin("idle-admin@sclub.test")
    prev = (settings.cycle_cadence, settings.cycle_start_date)
    object.__setattr__(settings, "cycle_cadence", "mwf")
    # cadence dormant until far future → cycle_phase is None everywhere → "idle"
    object.__setattr__(settings, "cycle_start_date", "2099-01-01")
    try:
        j = client.get("/api/admin/schedule", headers=_hdrs(tok)).json()
        assert j["today"]["phase"] == "idle"
        assert [row["phase"] for row in j["upcoming"]] == ["idle"] * 7
    finally:
        object.__setattr__(settings, "cycle_cadence", prev[0])
        object.__setattr__(settings, "cycle_start_date", prev[1])


def test_schedule_requires_admin():
    assert client.get("/api/admin/schedule", headers=_hdrs()).status_code == 401
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,created_at) "
            "VALUES(?,?,?,?,?)", ("sched-pleb@sclub.test", "pleb", "member",
                                  hash_password("sched-pass-11"), int(time.time())))
        mid = cur.lastrowid
    tok = make_session(mid, 1)
    assert client.get("/api/admin/schedule", headers=_hdrs(tok)).status_code == 403


# ─── GET /api/admin/bots ─────────────────────────────────────────────────────

def test_bots_roster_last_picks_and_llm_probe(monkeypatch):
    tok = _mk_admin("bots-admin@sclub.test")
    bot_a = _mk_bot("alpha@sbots.test", power=200.0, accuracy=0.35)
    bot_b = _mk_bot("beta@sbots.test", power=50.0)
    # Two sessions: last_picks must come from the NEWEST one, conviction-ordered.
    # Far-future trade_dates so no other module's scoring window can sweep them
    # up (the suite shares one DB), and 'executed' status keeps them inert.
    sid_old = _session("2031-01-06")
    sid_new = _session("2031-01-07")
    t_old, t_new = 1_900_000_000, 1_900_000_100
    with db() as conn:
        conn.execute(
            "INSERT INTO bot_picks(session_id,member_id,symbol,weight_pct,entry_price,"
            "thesis,created_at) VALUES(?,?,?,?,?,?,?)",
            (sid_old, bot_a, "AAPL", 100.0, 100.0, "old thesis", t_old))
        conn.executemany(
            "INSERT INTO bot_picks(session_id,member_id,symbol,weight_pct,entry_price,"
            "thesis,created_at) VALUES(?,?,?,?,?,?,?)",
            [(sid_new, bot_a, "NVDA", 60.0, 100.0, "new thesis", t_new),
             (sid_new, bot_a, "MSFT", 40.0, 100.0, "new thesis", t_new)])
        conn.executemany(
            "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
            "VALUES(?,?,?,?,?)",
            [(sid_old, bot_a, "AAPL", 100, t_old),
             (sid_new, bot_a, "NVDA", 60, t_new),
             (sid_new, bot_a, "MSFT", 40, t_new)])
    monkeypatch.setattr(ollama, "probe",
                        lambda timeout=2.0: {"reachable": True, "detail": "HTTP 200"})

    r = client.get("/api/admin/bots", headers=_hdrs(tok))
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"bots", "llm"}
    assert j["llm"] == {"reachable": True, "detail": "HTTP 200"}

    by_id = {b["id"]: b for b in j["bots"]}
    a, b = by_id[bot_a], by_id[bot_b]
    assert set(a) == {"id", "display_name", "voting_power", "accuracy",
                      "last_vote_at", "last_picks"}
    assert a["display_name"] == "alpha" and a["voting_power"] == 200.0
    assert a["accuracy"] == 0.35
    assert a["last_vote_at"] == t_new                    # newest vote, not the old one
    assert a["last_picks"] == ["NVDA", "MSFT"]           # newest session, weight DESC
    # A bot that has never voted still rosters — empty picks, null timestamp.
    assert b["voting_power"] == 50.0 and b["accuracy"] == 0.0
    assert b["last_vote_at"] is None and b["last_picks"] == []
    # Rows sort by voting power (other modules' bots may interleave — order is
    # relative): alpha (200) before beta (50).
    ids = [x["id"] for x in j["bots"]]
    assert ids.index(bot_a) < ids.index(bot_b)


def test_bots_endpoint_requires_admin():
    assert client.get("/api/admin/bots", headers=_hdrs()).status_code == 401


def test_ollama_probe_unreachable_never_raises():
    prev = settings.ollama_url
    # Port 9 (discard) on loopback: nothing listens → instant refusal, no network.
    object.__setattr__(settings, "ollama_url", "http://127.0.0.1:9")
    try:
        res = ollama.probe(timeout=0.5)
    finally:
        object.__setattr__(settings, "ollama_url", prev)
    assert res["reachable"] is False
    assert res["detail"]                                 # says WHY (exception name)


# ─── member_rows acct_type passthrough ───────────────────────────────────────

def test_member_rows_acct_type_passthrough():
    m_margin = _mk_human("margin-h@sclub.test")
    m_empty = _mk_human("empty-h@sclub.test")
    m_never = _mk_human("never-h@sclub.test")
    kv_set(f"acct_type:{m_margin}", "limited_margin")
    kv_set(f"acct_type:{m_empty}", "")                   # probed, type unknown

    rows = {m["id"]: m for m in adminview.member_rows()}
    assert rows[m_margin]["acct_type"] == "limited_margin"
    assert rows[m_empty]["acct_type"] is None            # empty string → null
    assert rows[m_never]["acct_type"] is None            # never probed → null

    # And it rides the panel endpoint (same builder the digest shares).
    tok = _mk_admin("acct-admin@sclub.test")
    p = client.get("/api/admin/panel", headers=_hdrs(tok)).json()
    api_rows = {m["id"]: m for m in p["members"]}
    assert api_rows[m_margin]["acct_type"] == "limited_margin"
    assert api_rows[m_never]["acct_type"] is None
