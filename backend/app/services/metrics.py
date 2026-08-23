"""Daily product/ops metrics — the tracking layer behind /api/admin/metrics.

AGGREGATE, DON'T DUPLICATE: the raw events already live in members,
login_events, votes, vote_sessions, executions and voting_power_history. This
module derives one `daily_metrics` row per ET calendar day from them, so the
table is a pure cache — `backfill()` can always rebuild every row from scratch,
and `upsert_day()` is idempotent (rerunning a day replaces the row).

STRICTLY DB-ONLY (same contract as services/adminview): nothing here may touch
Robinhood — every figure comes from rows the jobs already maintain, so the
nightly timer and the admin endpoint are both fast and side-effect free.

Definitions (all "human" filters = non-bot AND not self-service-deleted; the
deleted-% anonymization pattern matches adminview.member_rows):
- member_count: humans whose row existed by end of day (created_at < EOD ET).
- activation_count: humans who are fully set up — Robinhood connected
  (connected_at < EOD), opted in, and a positive stake. HONESTY NOTE: opt-in
  and stake are NOT historized, so backfilled days use the CURRENT flags with
  only the connection date time-bounded — history is approximate, today exact.
- dau / wau: distinct humans with a login event OR a cast vote that day /
  in the trailing 7 days ending that day.
- voters / eligible / ballot_participation: for the vote_session whose
  trade_date IS this day — human voters over humans who existed by EOD.
  All NULL on days with no session (weekends, off-cadence days).
- committed_capital_usd: sum over humans of the LATEST voting_power_history
  equity (fallback: power) with week_start <= the day — i.e. the capital the
  club's weekly power snapshot most recently observed as of that day.
- executed_notional_usd / error_count: that day's executor activity
  (status='placed' dollars, status='error' rows).
- waitlist_count: NULL — no waitlist table exists yet; the sqlite_master probe
  makes this light up automatically if one lands.
"""
from __future__ import annotations

import datetime as _dt
import time

from ..calendar_us import ET, today_et
from ..db import db

# Matches adminview.member_rows: self-service deletion anonymizes rows in place.
_HUMAN = ("COALESCE(m.is_bot,0)=0 "
          "AND m.email NOT LIKE 'deleted-%@deleted.invalid'")

# Safety valve for backfill: never reconstruct more than ~2 years even if a
# stray ancient timestamp sneaks into an event table.
_MAX_BACKFILL_DAYS = 730


def _day_bounds(d: _dt.date) -> tuple[int, int]:
    """[start, end) of the ET calendar day as epoch seconds."""
    start = _dt.datetime(d.year, d.month, d.day, tzinfo=ET)
    return int(start.timestamp()), int((start + _dt.timedelta(days=1)).timestamp())


def _has_waitlist(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='waitlist'"
    ).fetchone()
    return row is not None


def _active_humans(conn, start: int, end: int) -> int:
    """Distinct non-bot members with a login event or a cast vote in [start, end)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT DISTINCT e.member_id FROM ("
        "    SELECT member_id FROM login_events WHERE ts>=? AND ts<? "
        "    UNION ALL "
        "    SELECT member_id FROM votes WHERE created_at>=? AND created_at<? "
        "  ) e JOIN members m ON m.id=e.member_id "
        f"  WHERE {_HUMAN}"
        ")",
        (start, end, start, end),
    ).fetchone()
    return int(row["n"])


def compute_day(conn, d: _dt.date) -> dict:
    """One day's metrics, computed read-only. Keys mirror the table columns."""
    day = d.isoformat()
    start, end = _day_bounds(d)

    member_count = int(conn.execute(
        f"SELECT COUNT(*) AS n FROM members m WHERE {_HUMAN} AND m.created_at<?",
        (end,),
    ).fetchone()["n"])

    # Activation: connection is time-bounded (connected_at is recorded); opt-in
    # and stake are current-state — see the honesty note in the module docstring.
    activation_count = int(conn.execute(
        "SELECT COUNT(*) AS n FROM members m "
        "JOIN member_settings s ON s.member_id=m.id "
        "JOIN rh_accounts a ON a.member_id=m.id "
        f"WHERE {_HUMAN} AND m.created_at<? "
        "AND a.account_number IS NOT NULL AND a.status='connected' "
        "AND COALESCE(a.connected_at, 0)<? "
        "AND COALESCE(s.opted_in,0)=1 AND COALESCE(s.alloc_value,0)>0",
        (end, end),
    ).fetchone()["n"])

    dau = _active_humans(conn, start, end)
    wau = _active_humans(conn, end - 7 * 86400, end)

    voters = eligible = None
    participation = None
    sess = conn.execute(
        "SELECT id FROM vote_sessions WHERE trade_date=?", (day,)
    ).fetchone()
    if sess is not None:
        voters = int(conn.execute(
            "SELECT COUNT(DISTINCT v.member_id) AS n FROM votes v "
            f"JOIN members m ON m.id=v.member_id WHERE v.session_id=? AND {_HUMAN}",
            (sess["id"],),
        ).fetchone()["n"])
        eligible = member_count
        participation = (voters / eligible) if eligible else None

    committed = float(conn.execute(
        "SELECT COALESCE(SUM(COALESCE(v.equity, v.power)),0) AS s "
        "FROM voting_power_history v JOIN members m ON m.id=v.member_id "
        f"WHERE {_HUMAN} AND v.week_start = ("
        "  SELECT MAX(v2.week_start) FROM voting_power_history v2 "
        "  WHERE v2.member_id=v.member_id AND v2.week_start<=?"
        ")",
        (day,),
    ).fetchone()["s"])

    # Human rows only — today bots never reach the executor (no rh_accounts),
    # but a future bot execution path must not inflate the club's notional.
    ex = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN e.status='placed' THEN COALESCE(e.dollar_amount,0) END),0) AS notional, "
        "SUM(CASE WHEN e.status='error' THEN 1 ELSE 0 END) AS errs "
        "FROM executions e JOIN members m ON m.id=e.member_id "
        "WHERE e.created_at>=? AND e.created_at<? AND COALESCE(m.is_bot,0)=0",
        (start, end),
    ).fetchone()

    waitlist = None
    if _has_waitlist(conn):
        waitlist = int(conn.execute("SELECT COUNT(*) AS n FROM waitlist").fetchone()["n"])

    return {
        "date": day,
        "member_count": member_count,
        "activation_count": activation_count,
        "dau": dau,
        "wau": wau,
        "voters": voters,
        "eligible": eligible,
        "ballot_participation": participation,
        "committed_capital_usd": committed,
        "executed_notional_usd": float(ex["notional"] or 0),
        "error_count": int(ex["errs"] or 0),
        "waitlist_count": waitlist,
        "computed_at": int(time.time()),
    }


def upsert_day(d: _dt.date) -> dict:
    """Compute + write one day's row. Idempotent — rerunning replaces the row."""
    with db() as conn:
        row = compute_day(conn, d)
        conn.execute(
            "INSERT INTO daily_metrics(date, member_count, activation_count, dau, wau, "
            "voters, eligible, ballot_participation, committed_capital_usd, "
            "executed_notional_usd, error_count, waitlist_count, computed_at) "
            "VALUES(:date,:member_count,:activation_count,:dau,:wau,:voters,:eligible,"
            ":ballot_participation,:committed_capital_usd,:executed_notional_usd,"
            ":error_count,:waitlist_count,:computed_at) "
            "ON CONFLICT(date) DO UPDATE SET "
            "member_count=excluded.member_count, activation_count=excluded.activation_count, "
            "dau=excluded.dau, wau=excluded.wau, voters=excluded.voters, "
            "eligible=excluded.eligible, ballot_participation=excluded.ballot_participation, "
            "committed_capital_usd=excluded.committed_capital_usd, "
            "executed_notional_usd=excluded.executed_notional_usd, "
            "error_count=excluded.error_count, waitlist_count=excluded.waitlist_count, "
            "computed_at=excluded.computed_at",
            row,
        )
    return row


def earliest_data_date() -> _dt.date:
    """The earliest ET day any event table has data for (capped) — where a
    backfill should start. Falls back to today when the DB is empty."""
    with db() as conn:
        epochs = [
            conn.execute("SELECT MIN(ts) AS v FROM login_events").fetchone()["v"],
            conn.execute("SELECT MIN(created_at) AS v FROM votes").fetchone()["v"],
            conn.execute("SELECT MIN(created_at) AS v FROM executions").fetchone()["v"],
            conn.execute("SELECT MIN(created_at) AS v FROM members m "
                         f"WHERE {_HUMAN}").fetchone()["v"],
        ]
    epochs = [int(e) for e in epochs if e]
    today = today_et()
    if not epochs:
        return today
    first = _dt.datetime.fromtimestamp(min(epochs), tz=ET).date()
    floor = today - _dt.timedelta(days=_MAX_BACKFILL_DAYS)
    return max(first, floor)


def backfill() -> int:
    """Reconstruct daily_metrics from the earliest event to today. Idempotent
    (upsert per day) and safe to rerun; returns the number of days written."""
    d = earliest_data_date()
    today = today_et()
    n = 0
    while d <= today:
        upsert_day(d)
        n += 1
        d += _dt.timedelta(days=1)
    return n


def run_nightly() -> dict:
    """The nightly job body: first run (empty table) backfills all history so
    charts are never empty; steady state re-upserts yesterday + today (yesterday
    again because the job runs before midnight — late events land next run)."""
    with db() as conn:
        have = int(conn.execute("SELECT COUNT(*) AS n FROM daily_metrics").fetchone()["n"])
    if have == 0:
        days = backfill()
        return {"mode": "backfill", "days": days}
    today = today_et()
    upsert_day(today - _dt.timedelta(days=1))
    upsert_day(today)
    return {"mode": "nightly", "days": 2}


# ─── endpoint helpers (read side) ────────────────────────────────────────────

def series(days: int = 90) -> list[dict]:
    """The most recent `days` daily rows, oldest first (chart-ready)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def snapshot() -> dict:
    """Current-moment numbers = today's metrics computed LIVE (not the stored
    row, which may be from last night), plus an as_of stamp."""
    with db() as conn:
        row = compute_day(conn, today_et())
    row["as_of"] = int(time.time())
    return row


def retention_cohorts(weeks: int = 8, max_cohorts: int = 12) -> list[dict]:
    """Week-over-week voting retention by signup cohort.

    Cohort = the ET Monday of the member's signup week. retention[n] = the
    fraction of the cohort that cast at least one vote during week n of their
    OWN membership (n=0 is the first 7 days after signup). Weeks that haven't
    fully elapsed for the whole cohort are None, not 0 — absence of time is
    not churn.
    """
    now = int(time.time())
    with db() as conn:
        mems = conn.execute(
            f"SELECT m.id, m.created_at FROM members m WHERE {_HUMAN}"
        ).fetchall()
        votes = conn.execute(
            "SELECT DISTINCT v.member_id, v.created_at FROM votes v "
            f"JOIN members m ON m.id=v.member_id WHERE {_HUMAN}"
        ).fetchall()

    by_member: dict[int, list[int]] = {}
    for v in votes:
        by_member.setdefault(int(v["member_id"]), []).append(int(v["created_at"]))

    cohorts: dict[str, dict] = {}
    for m in mems:
        created = int(m["created_at"] or 0)
        if not created:
            continue
        d = _dt.datetime.fromtimestamp(created, tz=ET).date()
        monday = (d - _dt.timedelta(days=d.weekday())).isoformat()
        c = cohorts.setdefault(monday, {"size": 0, "voted": [set() for _ in range(weeks)],
                                        "youngest": created})
        c["size"] += 1
        c["youngest"] = max(c["youngest"], created)
        for ts in by_member.get(int(m["id"]), []):
            n = (ts - created) // (7 * 86400)
            if 0 <= n < weeks:
                c["voted"][n].add(int(m["id"]))

    out = []
    for monday in sorted(cohorts, reverse=True)[:max_cohorts]:
        c = cohorts[monday]
        retention: list[float | None] = []
        for n in range(weeks):
            # week n is only judged once every cohort member has lived it fully
            if c["youngest"] + (n + 1) * 7 * 86400 > now:
                retention.append(None)
            else:
                retention.append(len(c["voted"][n]) / c["size"] if c["size"] else None)
        out.append({"cohort": monday, "size": c["size"], "retention": retention})
    return out
