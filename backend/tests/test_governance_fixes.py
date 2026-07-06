"""Tests for the four audited governance fixes (A2, A3, A6, A9).

A2 — close_and_select is atomic: concurrent closers can't double-write picks.
A3 — MAX_NOTIONAL_PER_MEMBER (+ optional daily_cap_usd) clamp rebalance_budget.
A6 — bot power is capped per bot, and bots' aggregate tally share is capped.
A9 — voting power snapshots the INVESTED stake (total value), not idle cash.

Run from backend/:  python -m pytest tests/ -q
(uses a throwaway temp DB; no network / no Robinhood calls).
"""
import datetime as _dt
import os
import tempfile
import threading
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
# setdefault: when the whole suite runs, test_consensus.py (collected first)
# already pinned these and config is a frozen singleton — the values must agree.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import alloc, bots, market, power, voting  # noqa: E402


def setup_module(_):
    init_db()
    seed_universe()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, voting_power: float = 0.0, is_bot: int = 0,
            alloc_type: str = "percent", alloc_value: float = 0.0) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,bot_slug,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, email.split("@")[0], "bot" if is_bot else "member",
             is_bot, email.split("@")[0] if is_bot else None, int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (mid, 1, 0.0, 0.0, 10, voting_power, alloc_type, alloc_value, int(time.time())),
        )
    return mid


def _session(trade_date: str, status: str = "open", top_n: int = 3) -> int:
    """Insert a session row directly (bypasses the market calendar)."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,?)",
            (trade_date, status, int(time.time()), int(time.time()) + 3600, top_n),
        )
        return cur.lastrowid


def _sunday(d: _dt.date) -> _dt.date:
    """Normalize a date to its week's Sunday (matches power.week_start_et)."""
    return d - _dt.timedelta(days=(d.weekday() + 1) % 7)


# ─── A2: atomic close ────────────────────────────────────────────────────────

def test_racing_closers_produce_exactly_one_close():
    sid = _session("2026-07-06")
    m1 = _member("race1@x.com", voting_power=50.0)
    m2 = _member("race2@x.com", voting_power=30.0)
    voting.set_member_picks(sid, m1, ["AAPL", "NVDA"])
    voting.set_member_picks(sid, m2, ["AAPL", "MSFT"])

    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def closer():
        try:
            barrier.wait(timeout=10)
            results.append(voting.close_and_select(sid))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=closer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"closer raised: {errors}"
    assert len(results) == 2
    # Both callers see the SAME closed row: one status transition, one picks set.
    assert all(r["status"] == "closed" for r in results)
    assert results[0]["picks_json"] == results[1]["picks_json"]
    assert results[0]["closed_at"] == results[1]["closed_at"]
    picks = [p["symbol"] for p in voting.session_picks(results[0])]
    assert picks[0] == "AAPL"           # consensus winner intact

    with db() as conn:
        row = conn.execute("SELECT status FROM vote_sessions WHERE id=?", (sid,)).fetchone()
        n_closes = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE action='close_session' AND target=?",
            ("2026-07-06",),
        ).fetchone()["n"]
    assert row["status"] == "closed"
    assert n_closes == 1                # exactly one closer won


def test_close_is_idempotent_after_the_race():
    sid = _session("2026-07-07")
    m = _member("race3@x.com", voting_power=20.0)
    voting.set_member_picks(sid, m, ["AAPL"])
    first = voting.close_and_select(sid)
    again = voting.close_and_select(sid)
    assert first["status"] == again["status"] == "closed"
    assert first["picks_json"] == again["picks_json"]
    assert first["closed_at"] == again["closed_at"]


# ─── A3: notional caps ───────────────────────────────────────────────────────

def test_rebalance_budget_clamps_to_global_notional_cap():
    assert settings.max_notional_per_member == 2000.0
    # $50k dollar stake in a $100k account → clamped to the $2k global ceiling
    assert alloc.rebalance_budget("dollar", 50_000, 100_000, 100_000) == 2000.0
    # 100% of a $50k account → also clamped
    assert alloc.rebalance_budget("percent", 100.0, 50_000, 10_000) == 2000.0
    # Under the cap → unchanged behaviour
    assert alloc.rebalance_budget("dollar", 100.0, 100.76, 5.20) == 100.0
    assert alloc.rebalance_budget("percent", 10.0, 1_000, 500) == 100.0


def test_rebalance_budget_honours_member_daily_cap_when_passed():
    # daily_cap_usd > 0 clamps below the global ceiling
    assert alloc.rebalance_budget("dollar", 1_500, 10_000, None, daily_cap_usd=500.0) == 500.0
    # 0 / None = "no member cap" (schema default) → only the global cap applies
    assert alloc.rebalance_budget("dollar", 1_500, 10_000, None, daily_cap_usd=0.0) == 1500.0
    assert alloc.rebalance_budget("dollar", 1_500, 10_000, None, daily_cap_usd=None) == 1500.0


# ─── A6: bot power containment ───────────────────────────────────────────────

def test_bot_power_plateaus_at_cap(monkeypatch):
    cap = settings.bot_power_cap
    assert cap == 300.0
    bot = _member("plateau-bot@bots.concord", voting_power=100.0, is_bot=1)
    start_sunday = _sunday(_dt.date(2026, 8, 5))
    with db() as conn:
        conn.execute(
            "INSERT INTO voting_power_history(member_id,week_start,power,created_at) "
            "VALUES(?,?,?,?)",
            (bot, start_sunday.isoformat(), 100.0, int(time.time())),
        )
    # every pick is entered at 100 and quoted at 150 → +50%/week, every week
    monkeypatch.setattr(market, "quotes", lambda syms: {s: 150.0 for s in syms})

    expected, powers = 100.0, []
    for i in range(1, 11):
        prev_week = start_sunday + _dt.timedelta(weeks=i - 1)
        this_week = start_sunday + _dt.timedelta(weeks=i)
        sid = _session(prev_week.isoformat(), status="closed")
        with db() as conn:
            conn.execute(
                "INSERT INTO bot_picks(session_id,member_id,symbol,weight_pct,entry_price,"
                "thesis,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, bot, "AAPL", 100.0, 100.0, "up only", int(time.time())),
            )
        bots.score_and_reweight(week=this_week.isoformat())
        got = power.current_power(bot)
        powers.append(got)
        expected = min(cap, round(expected * 1.5, 2))
        assert got == expected, f"week {i}: power {got} != {expected}"
        assert got <= cap

    # compounding 100 → 150 → 225 → plateaus at the cap and stays there
    assert powers[:3] == [150.0, 225.0, 300.0]
    assert powers[-1] == cap and all(p == cap for p in powers[3:])


def test_tally_scales_bots_down_to_share_max():
    share = settings.bot_tally_share_max
    assert share == 0.90
    sid = _session("2026-07-08")
    human = _member("human-tally@x.com", voting_power=10.0)
    bot = _member("tally-bot@bots.concord", voting_power=990.0, is_bot=1)
    voting.set_member_picks(sid, human, ["AAPL"])                       # 10 on AAPL
    voting.set_member_allocations(sid, bot, [{"symbol": "AAPL", "weight": 50},
                                             {"symbol": "MSFT", "weight": 50}])  # 495 + 495

    ranked = voting.tally(sid)
    # shape preserved: exactly {symbol,name,votes,weight}, ranked by weight
    assert all(set(r.keys()) == {"symbol", "name", "votes", "weight"} for r in ranked)
    by_sym = {r["symbol"]: r for r in ranked}
    # raw bot share = 990/1000 = 0.99 → scaled so bots are exactly 90% of total:
    # f = 0.9·10 / (0.1·990) = 1/11 → AAPL = 10 + 495/11 = 55, MSFT = 495/11 = 45
    assert by_sym["AAPL"]["weight"] == 55.0       # human 10 UNSCALED + bot 45
    assert by_sym["MSFT"]["weight"] == 45.0
    assert ranked[0]["symbol"] == "AAPL" and ranked[0]["votes"] == 2
    total = sum(r["weight"] for r in ranked)
    bot_part = by_sym["AAPL"]["weight"] - 10.0 + by_sym["MSFT"]["weight"]
    assert abs(bot_part / total - share) < 1e-9


def test_tally_untouched_when_bots_within_share():
    sid = _session("2026-07-09")
    human = _member("human-tally2@x.com", voting_power=50.0)
    bot = _member("tally-bot2@bots.concord", voting_power=50.0, is_bot=1)
    voting.set_member_picks(sid, human, ["AAPL"])
    voting.set_member_picks(sid, bot, ["MSFT"])
    by_sym = {r["symbol"]: r for r in voting.tally(sid)}
    assert by_sym["AAPL"]["weight"] == 50.0 and by_sym["MSFT"]["weight"] == 50.0


# ─── A9: power = invested stake, not idle cash ───────────────────────────────

def test_snapshot_power_measures_invested_stake(monkeypatch):
    # the live-confirmed bug: $100 stake fully deployed → equity 100.76, idle
    # cash 5.20. Power must snapshot as 100.0 (the stake), not 5.20.
    mid = _member("owner-stake@x.com", alloc_type="dollar", alloc_value=100.0)
    monkeypatch.setattr(alloc, "member_balance",
                        lambda _mid: {"total_value": 100.76, "cash": 5.20, "buying_power": 5.20})
    snap = power.snapshot_member(mid, week="2026-08-02")
    assert snap is not None
    assert snap["power"] == 100.0
    assert snap["equity"] == 100.76
    assert power.current_power(mid) == 100.0
    with db() as conn:
        row = conn.execute(
            "SELECT power FROM voting_power_history WHERE member_id=? AND week_start=?",
            (mid, "2026-08-02"),
        ).fetchone()
    assert row["power"] == 100.0
    # resolve_allocation itself is unchanged — still caps at buying power,
    # which is correct for sizing an INITIAL buy.
    assert alloc.resolve_allocation("dollar", 100.0, 5.20) == 5.20
