"""Tests for the tracking layer: services/metrics + GET /api/admin/metrics.

Contract:
- compute/upsert_day: a seeded fixture day (humans + a bot + a deleted row,
  logins, a ballot, executions, weekly power snapshots) produces exactly the
  expected daily_metrics row — bots and deleted-% rows excluded everywhere,
  ballot fields NULL on no-session days, waitlist_count NULL (no table).
- Idempotence: rerunning upsert_day leaves ONE row with identical values.
- backfill/run_nightly: first run reconstructs the (capped) history; steady
  state re-upserts yesterday + today.
- retention_cohorts: signup-week cohorts over HUMAN votes only; weeks not yet
  fully elapsed are None, elapsed no-vote weeks are 0.0.
- Endpoint auth (require_admin): anon 401, plain member 403, admin/owner 200.

ISOLATION NOTE: the suite shares one process-wide DB, so every fixture here
lives on a fixed HISTORICAL day (2020-05-05 ET) — other modules' rows are all
created "now" and can't leak into 2020 windows (member_count/dau/etc. are
time-bounded by created_at/ts).

Run from backend/:  python -m pytest tests/test_metrics.py -q
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
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.calendar_us import ET, today_et  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import make_session  # noqa: E402
from app.services import metrics  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

DAY = dt.date(2020, 5, 5)          # Tuesday; 2020-05-04 was a Monday (cohort key)
_ip_counter = [0]


def _et(y, mo, d, h=12, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=ET).timestamp())


def _hdrs(tok: str | None = None) -> dict:
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.82.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, created_at: int, role: str = "member",
               is_bot: int = 0, opted_in: int = 0, alloc_value: float = 0.0,
               rh_connected_at: int | None = None) -> tuple[int, str]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at,"
            "email_verified_at) VALUES(?,?,?,?,?,?)",
            (email, email.split("@")[0], role, is_bot, created_at, created_at),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,?,0,0,25,0,'dollar',?,?)",
            (mid, opted_in, alloc_value, created_at),
        )
        if rh_connected_at is not None:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,"
                "status,connected_at) VALUES(?,?,1,'connected',?)",
                (mid, f"ACC{mid}", rh_connected_at),
            )
    return mid, make_session(mid, 1)


def setup_module(_):
    init_db()
    created = _et(2020, 5, 4)   # Monday noon ET — all fixture humans, one cohort

    global ALICE, BOB, BOT, DELETED, ALICE_TOK
    ALICE, ALICE_TOK = _mk_member("alice@metrics.test", created, opted_in=1,
                                  alloc_value=500.0, rh_connected_at=created)
    BOB, _ = _mk_member("bob@metrics.test", created)                 # not activated
    BOT, _ = _mk_member("bot@metrics.test", created, is_bot=1)
    DELETED, _ = _mk_member("deleted-9@deleted.invalid", created)    # anonymized row

    with db() as conn:
        # activity: alice logs in ON the day; bob logged in 3 days before
        # (inside the trailing-7-day WAU window, outside the day itself)
        conn.execute("INSERT INTO login_events(member_id,ts,kind) VALUES(?,?,'login')",
                     (ALICE, _et(2020, 5, 5, 9)))
        conn.execute("INSERT INTO login_events(member_id,ts,kind) VALUES(?,?,'login')",
                     (BOB, _et(2020, 5, 2, 9)))

        # the day's ballot: alice + the bot vote; bob abstains
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,top_n,opened_at) "
            "VALUES(?, 'closed', 3, ?)", (DAY.isoformat(), _et(2020, 5, 5, 10)))
        sid = cur.lastrowid
        for mid in (ALICE, BOT):
            conn.execute(
                "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
                "VALUES(?,?,?,100,?)", (sid, mid, "AAPL", _et(2020, 5, 5, 11)))

        # weekly power snapshots: alice has two weeks (latest wins), bob one,
        # the bot one (must be excluded from committed capital)
        for mid, week, equity in ((ALICE, "2020-04-26", 1000.0),
                                  (ALICE, "2020-05-03", 1200.0),
                                  (BOB, "2020-05-03", 800.0),
                                  (BOT, "2020-05-03", 99999.0)):
            conn.execute(
                "INSERT INTO voting_power_history(member_id,week_start,power,equity,"
                "created_at) VALUES(?,?,?,?,?)", (mid, week, equity, equity, _et(2020, 5, 3)))

        # executor activity on the day: two placed, one error, one skipped
        for status, amt in (("placed", 150.50), ("placed", 49.50),
                            ("error", None), ("skipped", 25.0)):
            conn.execute(
                "INSERT INTO executions(session_id,member_id,symbol,side,type,"
                "dollar_amount,status,created_at) VALUES(?,?,?,'buy','market',?,?,?)",
                (sid, ALICE, "AAPL", amt, status, _et(2020, 5, 5, 9, 31)))


# ─── aggregation correctness ─────────────────────────────────────────────────

def test_compute_day_on_seeded_fixture():
    row = metrics.upsert_day(DAY)
    assert row["date"] == "2020-05-05"
    assert row["member_count"] == 2            # alice + bob; bot + deleted excluded
    assert row["activation_count"] == 1        # alice: connected + opted in + staked
    assert row["dau"] == 1                     # alice (login + vote collapse to one)
    assert row["wau"] == 2                     # + bob's 05-02 login
    assert row["voters"] == 1                  # the bot's vote doesn't count
    assert row["eligible"] == 2
    assert abs(row["ballot_participation"] - 0.5) < 1e-9
    assert abs(row["committed_capital_usd"] - 2000.0) < 1e-9   # 1200 (latest) + 800
    assert abs(row["executed_notional_usd"] - 200.0) < 1e-9    # skipped $25 excluded
    assert row["error_count"] == 1
    assert row["waitlist_count"] is None       # no waitlist table exists


def test_no_ballot_day_has_null_participation():
    row = metrics.upsert_day(dt.date(2020, 5, 6))   # no session that day
    assert row["voters"] is None
    assert row["eligible"] is None
    assert row["ballot_participation"] is None
    assert row["member_count"] == 2                 # membership persists
    assert row["executed_notional_usd"] == 0.0


def test_upsert_is_idempotent():
    first = metrics.upsert_day(DAY)
    again = metrics.upsert_day(DAY)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_metrics WHERE date=?", (DAY.isoformat(),)
        ).fetchall()
    assert len(rows) == 1
    stable = {k: v for k, v in first.items() if k != "computed_at"}
    assert {k: v for k, v in again.items() if k != "computed_at"} == stable
    assert {k: v for k, v in dict(rows[0]).items() if k != "computed_at"} == stable


# ─── backfill + nightly ──────────────────────────────────────────────────────

def test_backfill_then_nightly():
    n = metrics.backfill()
    start = metrics.earliest_data_date()
    expected = (today_et() - start).days + 1
    assert n == expected
    with db() as conn:
        have = conn.execute("SELECT COUNT(*) AS n FROM daily_metrics").fetchone()["n"]
    assert have >= expected                    # + the 2020 fixture rows above
    # today + yesterday exist and rerunning stays idempotent
    result = metrics.run_nightly()
    assert result == {"mode": "nightly", "days": 2}
    with db() as conn:
        for d in (today_et(), today_et() - dt.timedelta(days=1)):
            assert conn.execute("SELECT 1 FROM daily_metrics WHERE date=?",
                                (d.isoformat(),)).fetchone() is not None


# ─── retention cohorts ───────────────────────────────────────────────────────

def test_retention_cohorts_signup_week():
    cohorts = {c["cohort"]: c for c in metrics.retention_cohorts(weeks=4, max_cohorts=500)}
    c = cohorts.get("2020-05-04")              # the fixture humans' signup Monday
    assert c is not None
    assert c["size"] == 2                      # bot + deleted excluded
    # alice voted the day after signup → week 0 retention = 1/2; every later
    # week has fully elapsed (2020) and no votes → 0.0, never None
    assert c["retention"][0] == 0.5
    assert all(r == 0.0 for r in c["retention"][1:])


# ─── endpoint auth + shape ───────────────────────────────────────────────────

def test_metrics_endpoint_requires_admin():
    assert client.get("/api/admin/metrics", headers=_hdrs()).status_code == 401
    assert client.get("/api/admin/metrics", headers=_hdrs(ALICE_TOK)).status_code == 403


def test_metrics_endpoint_admin_ok_and_shaped():
    now = int(time.time())
    _, admin_tok = _mk_member("ops@metrics.test", now, role="admin")
    r = client.get("/api/admin/metrics?days=30", headers=_hdrs(admin_tok))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"series", "snapshot", "cohorts", "generated_at"}
    assert isinstance(body["series"], list) and len(body["series"]) <= 30
    snap = body["snapshot"]
    assert snap["date"] == today_et().isoformat()
    assert "as_of" in snap and snap["member_count"] >= 2
    for c in body["cohorts"]:
        assert set(c) == {"cohort", "size", "retention"}
