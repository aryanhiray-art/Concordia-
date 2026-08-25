"""Tests for the blended voting-power model (ETHGlobal Concordia 50/50 formula).

Covers: entry-price capture, EWMA accuracy scoring (vote-weighted excess vs the
benchmark), cold-start == pure stake, accuracy redistribution, and bots (capital 0)
earning weight through accuracy only.

Run from backend/:  python -m pytest tests/test_reputation.py -q
"""
import dataclasses
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
    """AAPL +10%, benchmark +5% → vote-weighted excess +5% → two-timescale EWMA:
    short (H=13) and long (H=260) components each update from a 0 baseline, then
    blend 60/40. accuracy_long barely moves (long half-life) while accuracy_short
    carries most of the signal — this is the "recent form vs. career record" split."""
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
    with db() as conn:
        row = conn.execute(
            "SELECT accuracy, accuracy_short, accuracy_long, cycles_participated "
            "FROM member_settings WHERE member_id=?", (mid,)
        ).fetchone()
    assert row["accuracy_short"] == pytest.approx(0.0025961, abs=1e-6)
    assert row["accuracy_long"] == pytest.approx(0.0001331, abs=1e-6)
    assert row["accuracy"] == pytest.approx(0.0016109, abs=1e-6)   # 0.6*short + 0.4*long
    assert row["cycles_participated"] == 1
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


# ── two-timescale recency (equation 1 refinement) ────────────────────────────

def test_half_life_alpha_property():
    """alpha solves (1-alpha)**H == 0.5 exactly, for both configured half-lives."""
    from app.config import settings
    a_short = reputation._half_life_alpha(settings.accuracy_short_half_life_cycles)
    a_long = reputation._half_life_alpha(settings.accuracy_long_half_life_cycles)
    assert (1 - a_short) ** settings.accuracy_short_half_life_cycles == pytest.approx(0.5, abs=1e-9)
    assert (1 - a_long) ** settings.accuracy_long_half_life_cycles == pytest.approx(0.5, abs=1e-9)
    assert a_long < a_short   # the long component moves far slower per cycle


def test_long_component_preserves_old_signal_short_forgets_it():
    """Compare a pulse that landed 260 cycles ago (= the long half-life, "5 years"
    at a weekly cadence) against an equally-strong pulse landing THIS cycle: in
    accuracy_long the old pulse still carries ~50% as much weight as a fresh one
    (exactly one half-life of decay). In accuracy_short (13-cycle half-life) the
    same 260-cycle-old pulse has decayed to statistical noise (~20 half-lives).
    This relative-weight property — not the absolute EWMA value — is what makes
    'recent matters more, but a 5-year-old result still counts' true at once; a
    single EWMA's decay is one fixed exponential and can't do both."""
    from app.config import settings
    a_short = reputation._half_life_alpha(settings.accuracy_short_half_life_cycles)
    a_long = reputation._half_life_alpha(settings.accuracy_long_half_life_cycles)

    def _decayed(alpha: float, cycles_ago: int) -> float:
        contribution = alpha * 0.10   # the pulse's initial EWMA contribution
        return contribution * (1 - alpha) ** cycles_ago

    long_old, long_now = _decayed(a_long, 260), _decayed(a_long, 0)
    short_old, short_now = _decayed(a_short, 260), _decayed(a_short, 0)
    assert long_old / long_now == pytest.approx(0.5, rel=1e-6)     # exactly one long half-life
    assert short_old / short_now < 1e-5                            # ~20 short half-lives — negligible


# ── organic capital dampening (equation 1 refinement) ────────────────────────

def test_capital_gamma_dampens_whale_influence(monkeypatch):
    """4x the stake should buy ~2x the power at gamma=0.5 (sqrt), not 4x — the
    'organic, gets harder to get more' curve, with no hard cap or cliff."""
    from app.config import settings
    dampened = dataclasses.replace(settings, capital_gamma_bps=5000)   # gamma=0.5
    monkeypatch.setattr(reputation, "settings", dampened)
    whale = _member("whale@x.com", alloc_value=400.0)
    minnow = _member("minnow@x.com", alloc_value=100.0)
    _mock_balance(monkeypatch, {whale: {"total_value": 10000, "buying_power": 10000},
                                minnow: {"total_value": 10000, "buying_power": 10000}})
    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    # cap_share ratio = sqrt(400):sqrt(100) = 20:10 = 2:1, not the raw 4:1 stake ratio.
    # rel=1e-3 (not exact) because production rounds power to cents.
    assert snaps[whale]["power"] / snaps[minnow]["power"] == pytest.approx(2.0, rel=1e-3)


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


def test_bot_fake_stake_participates_like_human(monkeypatch):
    """Bots carry a FAKE stake (BOT_STAKE_USD) so they vote as funded members: cold
    start → power == the fake stake; accuracy then tilts it exactly like a human."""
    from app.config import settings
    human = _member("bz-h@x.com", alloc_value=100.0)
    bot = _member("bz-bot@x.com", is_bot=1, connect=False)
    _mock_balance(monkeypatch, {human: {"total_value": 1000, "buying_power": 1000}})

    cold = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    assert cold[bot]["capital"] == pytest.approx(settings.bot_stake_usd)   # fake stake, not 0
    assert cold[bot]["power"] == pytest.approx(100.0)                      # cold start == its stake
    assert cold[human]["power"] == pytest.approx(100.0)

    _set(bot, accuracy=0.1, cycles_participated=12)                        # bot proves accuracy
    _set(human, accuracy=0.0, cycles_participated=12)
    hot = {s["member_id"]: s for s in reputation.snapshot_power_all()}
    assert hot[bot]["power"] == pytest.approx(150.0)                       # stake share + accuracy half
    assert hot[human]["power"] == pytest.approx(50.0)


# ─── fail-safe snapshot + scoring gates (re-audit ranks 4, 10) ───────────────

def test_snapshot_skips_member_on_failed_balance_read(monkeypatch):
    """A CONNECTED member whose live balance read FAILS is SKIPPED — prior power is
    carried, never overwritten with 0 (fail-safe, not the fail-open regression)."""
    a = _member("fs-a@x.com", alloc_value=100.0)
    b = _member("fs-b@x.com", alloc_value=100.0)
    _set(b, voting_power=77.0)                       # B has prior power to preserve
    _mock_balance(monkeypatch, {a: {"total_value": 1000, "buying_power": 1000}})  # B absent → None (failed read)

    snaps = {s["member_id"]: s for s in reputation.snapshot_power_all()}

    assert a in snaps and b not in snaps             # A written, B skipped
    with db() as c:
        vp = c.execute("SELECT voting_power FROM member_settings WHERE member_id=?", (b,)).fetchone()["voting_power"]
        zeros = c.execute("SELECT COUNT(*) n FROM voting_power_history WHERE member_id=? AND power=0", (b,)).fetchone()["n"]
    assert vp == pytest.approx(77.0)                 # carried forward, NOT zeroed
    assert zeros == 0                                # no power=0 history row


def test_score_skips_non_measurement(monkeypatch):
    """used==0 (no pick had both an entry AND exit price) → accuracy + cycles unchanged."""
    mid = _member("nm@x.com")
    _set(mid, accuracy=0.08, cycles_participated=5)
    s1 = voting.open_session_for(datetime.date(2027, 6, 1))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 200.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 6, 3))   # AAPL NOT re-voted → no exit price
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"SPY": 420.0})
    reputation.capture_cycle_prices(s2["id"])

    reputation.score_matured_cycles()

    acc, cyc = _acc(mid)
    assert acc == pytest.approx(0.08) and cyc == 5   # non-measurement did NOT decay/ramp


def test_score_skips_cycle_when_benchmark_missing(monkeypatch):
    """No benchmark price → the cycle is NOT sealed (scored_at stays NULL) so it retries,
    instead of silently scoring raw return."""
    mid = _member("bench@x.com")
    _set(mid, accuracy=0.05, cycles_participated=3)
    s1 = voting.open_session_for(datetime.date(2027, 6, 8))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 200.0})   # NO SPY
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 6, 10))
    voting.set_member_picks(s2["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 220.0})   # NO SPY
    reputation.capture_cycle_prices(s2["id"])

    reputation.score_matured_cycles()

    with db() as c:
        scored_at = c.execute("SELECT scored_at FROM vote_sessions WHERE id=?", (s1["id"],)).fetchone()["scored_at"]
    acc, cyc = _acc(mid)
    assert scored_at is None                         # not sealed → will retry with real benchmark
    assert acc == pytest.approx(0.05) and cyc == 3   # untouched


def test_two_timescale_migration_seeds_from_accumulated_accuracy():
    """Adding accuracy_short/accuracy_long must SEED them from the existing
    single-EWMA score. Blending from 0 would collapse every member's history
    to a one-cycle value on the next scoring run while cycles_participated
    kept confidence maxed — full weight on a score with no history behind it."""
    from app.db import _migrate, db, init_db
    init_db()
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO members(id,email,display_name,role,created_at) "
                     "VALUES(9411,'seed@rep.test','seed','member',0)")
        conn.execute("INSERT OR REPLACE INTO member_settings"
                     "(member_id,opted_in,buying_power_pct,daily_cap_usd,max_orders,"
                     " voting_power,alloc_type,alloc_value,accuracy,accuracy_short,"
                     " accuracy_long,cycles_participated,updated_at) "
                     "VALUES(9411,1,0,0,25,0,'dollar',100,0.0412,0,0,7,0)")
        _migrate(conn)
        row = conn.execute("SELECT accuracy, accuracy_short, accuracy_long FROM "
                           "member_settings WHERE member_id=9411").fetchone()
    assert row["accuracy_short"] == pytest.approx(0.0412)
    assert row["accuracy_long"] == pytest.approx(0.0412)
    # continuity: the blend of the seeded pair reproduces the original score
    from app.services.reputation import _blend_accuracy
    assert _blend_accuracy(row["accuracy_short"], row["accuracy_long"]) == pytest.approx(0.0412)
