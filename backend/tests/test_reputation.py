"""Tests for the blended voting-power model (ETHGlobal Concordia 50/50 formula).

Covers: entry-price capture, EWMA accuracy scoring (vote-weighted excess vs the
benchmark), cold-start == pure stake, accuracy redistribution, and bots (capital 0)
earning weight through accuracy only.

Run from backend/:  python -m pytest tests/test_reputation.py -q
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

from app.db import db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import reputation, voting  # noqa: E402


def setup_module(_):
    init_db()
    seed_universe()
    # This module snapshots ALL members, so start from a clean slate (it runs last
    # alphabetically; wiping doesn't affect earlier modules).
    with db() as conn:
        for t in ("executions", "votes", "bot_picks", "cycle_prices", "vote_sessions",
                  "voting_power_history", "login_events", "api_keys", "audit_log",
                  "rh_accounts", "member_settings", "members"):
            conn.execute(f"DELETE FROM {t}")


_MUTABLE = ("executions", "votes", "bot_picks", "cycle_prices", "vote_sessions",
            "voting_power_history", "login_events", "api_keys", "audit_log",
            "rh_accounts", "member_settings", "members")


@pytest.fixture(autouse=True)
def _isolate():
    """snapshot_power_all() scans ALL members, so wipe between tests in this module
    (members from one test would otherwise skew another's renormalization)."""
    with db() as conn:
        for t in _MUTABLE:
            conn.execute(f"DELETE FROM {t}")
    yield


def _member(email, alloc_type="dollar", alloc_value=100.0, is_bot=0, connect=True):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) VALUES(?,?,?,?,?)",
            (email, email.split("@")[0], "member", is_bot, int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (mid, 1, 10.0, 0.0, 10, alloc_type, alloc_value, int(time.time())),
        )
        if connect and not is_bot:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
                "connected_at,last_refresh_at,status) VALUES(?,?,?,?,?,?,?)",
                (mid, f"ACC{mid}", 1, b"enc", int(time.time()), int(time.time()), "connected"),
            )
    return mid


def _set(mid, **kw):
    with db() as conn:
        for k, v in kw.items():
            conn.execute(f"UPDATE member_settings SET {k}=? WHERE member_id=?", (v, mid))


def _acc(mid):
    with db() as conn:
        r = conn.execute("SELECT accuracy, cycles_participated FROM member_settings WHERE member_id=?",
                         (mid,)).fetchone()
    return r["accuracy"], r["cycles_participated"]


# ── accuracy scoring ─────────────────────────────────────────────────────────

def test_capture_and_score_accuracy(monkeypatch):
    """AAPL +10%, benchmark +5% → vote-weighted excess +5% → EWMA(0.25) accuracy 0.0125."""
    mid = _member("rep-score@x.com")
    s1 = voting.open_session_for(datetime.date(2027, 5, 4))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 200.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])

    s2 = voting.open_session_for(datetime.date(2027, 5, 6))
    voting.set_member_picks(s2["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 220.0, "SPY": 420.0})
    reputation.capture_cycle_prices(s2["id"])

    scored = reputation.score_matured_cycles()

    assert len(scored) == 1 and scored[0]["scored_session"] == s1["id"]
    acc, cycles = _acc(mid)
    assert acc == pytest.approx(0.0125, abs=1e-4)     # 0.25 * 0.05
    assert cycles == 1
    # s2 has no later cycle yet → not matured → not double-scored
    assert reputation.score_matured_cycles() == []
    assert _acc(mid)[1] == 1


def test_score_is_paper_basis_excess_vs_benchmark(monkeypatch):
    """Backing a name that only MATCHED the benchmark earns ~0 accuracy; beating it earns +."""
    mid = _member("rep-excess@x.com")
    s1 = voting.open_session_for(datetime.date(2027, 5, 11))
    voting.set_member_picks(s1["id"], mid, ["KO"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"KO": 100.0, "SPY": 100.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 5, 13))
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"KO": 108.0, "SPY": 108.0})  # KO == bench
    reputation.capture_cycle_prices(s2["id"])

    reputation.score_matured_cycles()

    assert _acc(mid)[0] == pytest.approx(0.0, abs=1e-6)   # matched benchmark → no excess


# ── blended snapshot ─────────────────────────────────────────────────────────

def _mock_balance(monkeypatch, by_mid):
    monkeypatch.setattr(reputation.alloc, "member_balance", lambda mid: by_mid.get(mid))


def test_cold_start_equals_stake(monkeypatch):
    """No accuracy yet → blend reduces to pure committed stake."""
    a = _member("cs-a@x.com", alloc_value=100.0)
    b = _member("cs-b@x.com", alloc_value=50.0)
    _mock_balance(monkeypatch, {a: {"total_value": 1000, "buying_power": 1000},
                                b: {"total_value": 1000, "buying_power": 1000}})
    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    assert snaps[a]["power"] == pytest.approx(100.0)   # dollar $100 stake
    assert snaps[b]["power"] == pytest.approx(50.0)
    assert snaps[a]["accuracy"] == 0.0 and snaps[a]["confidence"] == 0.0


def test_accuracy_redistributes_power(monkeypatch):
    """Equal $100 stakes, A is accurate at full confidence → A gets 150, B gets 50."""
    a = _member("ar-a@x.com", alloc_value=100.0)
    b = _member("ar-b@x.com", alloc_value=100.0)
    _set(a, accuracy=0.1, cycles_participated=12)
    _set(b, accuracy=0.0, cycles_participated=12)
    _mock_balance(monkeypatch, {a: {"total_value": 1000, "buying_power": 1000},
                                b: {"total_value": 1000, "buying_power": 1000}})
    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    assert snaps[a]["power"] == pytest.approx(150.0)
    assert snaps[b]["power"] == pytest.approx(50.0)


def test_confidence_ramp_damps_new_members(monkeypatch):
    """Same accuracy but only 6/12 cycles → accuracy counts at half weight."""
    a = _member("cr-a@x.com", alloc_value=100.0)
    b = _member("cr-b@x.com", alloc_value=100.0)
    _set(a, accuracy=0.1, cycles_participated=6)    # confidence 0.5
    _set(b, accuracy=0.0, cycles_participated=12)
    _mock_balance(monkeypatch, {a: {"total_value": 1000, "buying_power": 1000},
                                b: {"total_value": 1000, "buying_power": 1000}})
    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    # A raw = 0.5*0.5 + 0.5*1*0.5 = 0.5 ; B raw = 0.25 ; total 0.75 ; pool 200
    assert snaps[a]["power"] == pytest.approx(200 * 0.5 / 0.75, abs=0.05)   # ~133.3
    assert snaps[b]["power"] == pytest.approx(200 * 0.25 / 0.75, abs=0.05)  # ~66.7
    assert snaps[a]["confidence"] == pytest.approx(0.5)


def test_bot_capital_zero_earns_via_accuracy(monkeypatch):
    """A bot holds no capital → weight comes purely from accuracy (× confidence)."""
    human = _member("bz-h@x.com", alloc_value=100.0)
    bot = _member("bz-bot@x.com", is_bot=1, connect=False)
    _set(bot, accuracy=0.1, cycles_participated=12)
    _set(human, accuracy=0.0, cycles_participated=12)
    _mock_balance(monkeypatch, {human: {"total_value": 1000, "buying_power": 1000}})
    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    assert snaps[bot]["capital"] == 0.0
    assert snaps[bot]["power"] == pytest.approx(50.0)     # accuracy half of the $100 human pool
    assert snaps[human]["power"] == pytest.approx(50.0)
