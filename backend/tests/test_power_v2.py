"""Voting power v2 — the merit form, the exit theorem, and the lifecycle.

The properties here are the REASON for the redesign, so they are asserted as
properties rather than as golden numbers wherever possible:

  * cold start is exact       — an unscored club weighs exactly its dollars
  * exit is a renormalisation — removing ANY member preserves every survivor's
                                pairwise ratio and rank EXACTLY (the legacy
                                additive form fails this: measured on the live
                                roster, ratios drift up to 24.7% and ranks flip)
  * merit is bounded          — best:worst can never exceed MERIT_MAX_MULTIPLE**2
  * no zero-cliff             — unproven and proven-bad are different numbers
  * R1 inertia                — the score slows as the club grows, one-sided
  * departed members are inert — no power, no ballot weight, nothing reset

Sorts after test_reputation.py (whose autouse fixture wipes members) and claims
its own session dates — `open_session_for` is idempotent, so reusing another
module's date silently merges votes into that module's tally.

Run from backend/:  python -m pytest tests/test_power_v2.py -q
"""
import dataclasses
import datetime
import itertools
import math
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

from app.config import invalid_power_config, settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import membership, reputation, voting  # noqa: E402

_MUTABLE = ("executions", "votes", "bot_picks", "cycle_prices", "cycle_scores",
            "vote_sessions", "voting_power_history", "login_events", "api_keys",
            "audit_log", "rh_accounts", "member_settings", "members")


def setup_module(_):
    init_db()
    seed_universe()
    with db() as conn:
        for t in _MUTABLE:
            conn.execute(f"DELETE FROM {t}")


# Settings is a FROZEN dataclass, so it is replaced wholesale and re-bound in
# every module that captured it at import time (the repo's existing idiom).
# `_CUR` carries the module default forward so a per-test override composes with
# it instead of resetting to the process defaults.
_CUR: list = []


def _bind(monkeypatch, cfg):
    import app.config as cfgmod
    for mod in (cfgmod, reputation, voting):
        monkeypatch.setattr(mod, "settings", cfg, raising=False)
    _CUR[:] = [cfg]
    return cfg


def _cfg(monkeypatch, **kw):
    """Override knobs on top of this module's defaults."""
    return _bind(monkeypatch, dataclasses.replace(_CUR[0], **kw))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    with db() as conn:
        for t in _MUTABLE:
            conn.execute(f"DELETE FROM {t}")
    # v2 form on by default in this module; the legacy control test opts back out.
    _CUR[:] = [settings]
    _bind(monkeypatch, dataclasses.replace(settings, power_form="merit", power_model="blend"))
    yield


def _member(email, capital=100.0, accuracy=0.0, cycles=0, is_bot=0, connect=True):
    """A member whose committed capital is `capital` dollars."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) VALUES(?,?,?,?,?)",
            (email, email.split("@")[0], "member", is_bot, int(time.time())))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,alloc_type,alloc_value,accuracy,accuracy_short,accuracy_long,"
            "cycles_participated,updated_at) VALUES(?,1,10.0,0.0,10,'dollar',?,?,?,?,?,?)",
            (mid, capital, accuracy, accuracy, accuracy, cycles, int(time.time())))
        if connect and not is_bot:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
                "connected_at,last_refresh_at,status) VALUES(?,?,1,?,?,?,'connected')",
                (mid, f"ACC{mid}", b"enc", int(time.time()), int(time.time())))
    return mid


@pytest.fixture
def balances(monkeypatch):
    """Make every member's brokerage read succeed with plenty of headroom, so
    committed capital resolves to their alloc_value."""
    monkeypatch.setattr(reputation.alloc, "member_balance",
                        lambda mid: {"total_value": 1_000_000.0, "buying_power": 1_000_000.0})
    return monkeypatch


def _snap():
    return {r["member_id"]: r for r in reputation.snapshot_power_all()}


# ── cold start ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mm", [1.5, 2.0, 5.0])
def test_cold_start_is_exactly_committed_capital(balances, monkeypatch, mm):
    """With no accuracy anywhere, v2 must pay out exactly the dollars committed.

    This is what makes turning the form on safe: an unscored club sees zero
    change. It holds for ANY MERIT_MAX_MULTIPLE because tanh(0)==0 ⇒ M==1.0.
    """
    _cfg(monkeypatch, merit_max_multiple=mm)
    a, b, c = _member("cold-a@x.com", 100.0), _member("cold-b@x.com", 50.0), _member("cold-c@x.com", 1000.0)
    out = _snap()
    assert out[a]["power"] == 100.0
    assert out[b]["power"] == 50.0
    assert out[c]["power"] == 1000.0
    assert all(o["merit"] == 1.0 for o in out.values())


def test_merit_multiplier_is_exactly_one_at_zero():
    """Not approximately — exactly. Cold start depends on it being bit-exact."""
    assert reputation.merit_multiplier(0.0) == 1.0


# ── R2: bounded, monotone, no cliff ──────────────────────────────────────────

def test_merit_is_bounded_for_any_input(monkeypatch):
    """No accuracy — however extreme, however corrupt — can produce inf/nan or
    escape [1/MM, MM]. tanh saturates, so the bound is a priori."""
    _cfg(monkeypatch, merit_max_multiple=2.0)
    lo, hi = 1 / 2.0, 2.0
    for a in (-1e9, -1.0, -0.02, -1e-9, 0.0, 1e-9, 0.02, 1.0, 1e9):
        m = reputation.merit_multiplier(a)
        assert math.isfinite(m) and lo <= m <= hi, (a, m)


def test_merit_is_strictly_increasing_with_no_cliff_at_zero(monkeypatch):
    """The defect this replaces: max(acc,0) collapsed everyone at or below zero
    onto ONE number, so a member one basis point underwater scored identically
    to one seventy times worse, and identically to a member with no record."""
    _cfg(monkeypatch, merit_max_multiple=2.0)
    xs = [i * 1e-5 for i in range(-200, 201)]
    ms = [reputation.merit_multiplier(x) for x in xs]
    assert all(b > a for a, b in zip(ms, ms[1:]))
    # the live trio that used to be indistinguishable
    assert (reputation.merit_multiplier(-0.00716)
            < reputation.merit_multiplier(-0.00010)
            < reputation.merit_multiplier(0.0))


def test_being_wrong_costs_power_and_abstaining_is_not_a_dodge(balances):
    """At equal capital: wrong < unproven < right, strictly. Under the legacy
    form 'wrong' and 'unproven' were the same number."""
    bad = _member("v2-bad@x.com", 100.0, accuracy=-0.007, cycles=8)
    new = _member("v2-new@x.com", 100.0, accuracy=0.0, cycles=0)
    good = _member("v2-good@x.com", 100.0, accuracy=+0.007, cycles=8)
    out = _snap()
    assert out[bad]["power"] < out[new]["power"] < out[good]["power"]


def test_spread_compresses_but_ordering_survives(balances, monkeypatch):
    """R2 in one assertion: lowering MERIT_MAX_MULTIPLE brings everyone closer
    together WITHOUT making them equal and WITHOUT re-ranking anyone."""
    accs = [0.00959, 0.00636, 0.00584, 0.00221, -0.00110, -0.00716]
    mids = [_member(f"v2-spread{i}@x.com", 100.0, accuracy=a, cycles=6)
            for i, a in enumerate(accs)]
    spreads, orders = [], []
    for mm in (3.0, 2.0, 1.5, 1.05):
        _cfg(monkeypatch, merit_max_multiple=mm)
        out = _snap()
        powers = [out[m]["power"] for m in mids]
        spreads.append(max(powers) / min(powers))
        orders.append(sorted(mids, key=lambda m: -out[m]["power"]))
        assert len(set(powers)) == len(powers), "compression must not make members EQUAL"
    assert spreads == sorted(spreads, reverse=True), "lower MM must compress"
    assert all(o == orders[0] for o in orders), "compression must never re-rank"


def test_accuracy_rank_is_preserved_at_equal_capital(balances):
    """Among equal-capital members, order by power == order by accuracy. The
    legacy confidence multiplier broke this: it multiplied accuracy share by
    tenure, so a longer-serving member with a WORSE record outranked a better one."""
    accs = [0.00959, 0.00636, 0.00584, 0.00396, 0.00221, -0.00110, -0.00374, -0.00716]
    # cycles deliberately ANTI-correlated with accuracy — the legacy form inverts here
    mids = [_member(f"v2-rank{i}@x.com", 100.0, accuracy=a, cycles=12 - i)
            for i, a in enumerate(accs)]
    out = _snap()
    assert sorted(mids, key=lambda m: -out[m]["power"]) == mids


# ── R3: the exit theorem ─────────────────────────────────────────────────────

def _roster(balances):
    """A deliberately heterogeneous club: one whale, small members, bots, good
    and bad records, an unproven member."""
    spec = [("whale", 1000.0, 0.0, 0), ("owner", 100.09, 0.00100, 11),
            ("small", 20.87, 0.0, 0), ("tiny", 9.68, 0.0, 0),
            ("star", 100.0, 0.00959, 6), ("good", 100.0, 0.00584, 8),
            ("meh", 100.0, 0.00122, 7), ("bad", 100.0, -0.00374, 7),
            ("worst", 100.0, -0.00716, 6)]
    return {n: _member(f"v2-{n}@x.com", c, accuracy=a, cycles=cy) for n, c, a, cy in spec}


def test_exit_is_a_pure_renormalisation_for_every_possible_leaver(balances):
    """THE requirement. For EVERY member as the leaver: every survivor's share is
    exactly share_i/(1 - share_leaver), every pairwise ratio is unchanged, and
    nobody is re-ranked. Nothing about the survivors is recomputed."""
    ids = _roster(balances)
    before = {mid: r["share"] for mid, r in _snap().items()}
    for name, leaver in ids.items():
        membership.set_left(leaver, "test", "exit-invariant")
        after = {mid: r["share"] for mid, r in _snap().items()}
        assert leaver not in after, f"{name} still counted after leaving"
        f = 1.0 - before[leaver]
        for mid, s in after.items():
            assert abs(s - before[mid] / f) < 1e-12, (name, mid)
        for i, j in itertools.combinations(after, 2):
            assert abs((after[i] / after[j]) / (before[i] / before[j]) - 1) < 1e-9
        survivors = [m for m in before if m != leaver]
        assert (sorted(survivors, key=lambda m: -before[m])
                == sorted(survivors, key=lambda m: -after[m])), f"{name} exit re-ranked survivors"
        membership.clear_left(leaver, "test")


def _pool(mid):
    with db() as conn:
        return conn.execute("SELECT pool_usd FROM voting_power_history WHERE member_id=? "
                            "ORDER BY week_start DESC LIMIT 1", (mid,)).fetchone()["pool_usd"]


def test_exit_moves_every_survivor_by_one_uniform_dollar_factor(balances):
    """Because the factor is common, an exit renders as ONE club-level pool
    change — not as 8 members each appearing to have personally lost money.

    The factor is exact on the canonical (unrounded) share:
        VP'_i / VP_i  =  (P' / P) / (1 - share_leaver)
    Dollars are rounded to cents for display, so the assertion is that every
    survivor lands within one cent of that common factor — NOT that the rounded
    ratios agree to 1e-9, which a $9 member can never satisfy.
    """
    ids = _roster(balances)
    snap0 = _snap()
    before = {mid: r["power"] for mid, r in snap0.items()}
    pool0 = _pool(ids["owner"])
    leaver_share = snap0[ids["whale"]]["share"]
    membership.set_left(ids["whale"], "test", "uniform-factor")
    after = {mid: r["power"] for mid, r in _snap().items()}
    pool1 = _pool(ids["owner"])
    f = (pool1 / pool0) / (1.0 - leaver_share)
    for mid, vp in after.items():
        assert abs(vp - before[mid] * f) <= 0.01, (mid, vp, before[mid] * f)


def test_an_exit_writes_no_survivor_state(balances):
    """'Rescaled, not reset': a departure must not touch anyone's record."""
    ids = _roster(balances)
    _snap()
    with db() as conn:
        pre = {r["member_id"]: tuple(r)[1:] for r in conn.execute(
            "SELECT member_id, accuracy, accuracy_short, accuracy_long, cycles_participated "
            "FROM member_settings").fetchall()}
        hist_pre = conn.execute("SELECT COUNT(*) c FROM voting_power_history").fetchone()["c"]
    membership.set_left(ids["star"], "test", "no-reset")
    _snap()
    with db() as conn:
        post = {r["member_id"]: tuple(r)[1:] for r in conn.execute(
            "SELECT member_id, accuracy, accuracy_short, accuracy_long, cycles_participated "
            "FROM member_settings").fetchall()}
        hist_post = conn.execute("SELECT COUNT(*) c FROM voting_power_history").fetchone()["c"]
    assert post == pre, "a departure rewrote someone's reputation"
    assert hist_post >= hist_pre, "a departure deleted history"


def test_join_is_the_mirror_of_exit(balances):
    """Adding a member multiplies every incumbent's share by one common factor."""
    _roster(balances)
    before = {mid: r["share"] for mid, r in _snap().items()}
    _member("v2-joiner@x.com", 2000.0, accuracy=0.0, cycles=0)
    after = {mid: r["share"] for mid, r in _snap().items()}
    factors = [after[m] / before[m] for m in before]
    assert max(factors) - min(factors) < 1e-12, factors


def test_simultaneous_departures_still_renormalise(balances):
    ids = _roster(balances)
    before = {mid: r["share"] for mid, r in _snap().items()}
    gone = [ids["whale"], ids["bad"], ids["tiny"]]
    for g in gone:
        membership.set_left(g, "test", "multi")
    after = {mid: r["share"] for mid, r in _snap().items()}
    f = 1.0 - sum(before[g] for g in gone)
    for mid, s in after.items():
        assert abs(s - before[mid] / f) < 1e-12


def test_legacy_form_fails_the_exit_invariant(balances, monkeypatch):
    """The control. Documents WHY the form changed: under the additive legacy
    form the same exit re-weights survivors non-uniformly."""
    _cfg(monkeypatch, power_form="legacy")
    ids = _roster(balances)
    before = {mid: r["power"] for mid, r in _snap().items()}
    membership.set_left(ids["whale"], "test", "legacy-control")
    after = {mid: r["power"] for mid, r in _snap().items()}
    factors = [after[m] / before[m] for m in after]
    assert max(factors) - min(factors) > 0.01, (
        "legacy is expected to move survivors by DIFFERENT factors")


# ── membership lifecycle ─────────────────────────────────────────────────────

def test_departed_member_holds_no_power_and_no_ballot_weight(balances):
    stay = _member("v2-stay@x.com", 100.0)
    goer = _member("v2-goer@x.com", 900.0)
    s = voting.open_session_for(datetime.date(2027, 9, 7))
    voting.set_member_picks(s["id"], stay, ["AAPL"])
    voting.set_member_picks(s["id"], goer, ["MSFT"])
    _snap()
    assert {t["symbol"] for t in voting.tally(s["id"])} == {"AAPL", "MSFT"}
    membership.set_left(goer, "test", "departed")
    out = _snap()
    assert goer not in out and stay in out
    assert {t["symbol"] for t in voting.tally(s["id"])} == {"AAPL"}, (
        "a departed member's ballot still weighed on the basket")


def test_anonymized_rows_are_departed_even_without_left_at(balances):
    """Belt-and-braces: rows anonymized before left_at existed must stay out."""
    keep = _member("v2-keep@x.com", 100.0)
    ghost = _member("v2-ghost@x.com", 500.0)
    with db() as conn:
        conn.execute("UPDATE members SET email=? WHERE id=?",
                     (f"deleted-{ghost}@deleted.invalid", ghost))
    out = _snap()
    assert ghost not in out and keep in out


def test_reinstated_member_returns_with_record_intact(balances):
    ids = _roster(balances)
    before = _snap()
    membership.set_left(ids["star"], "test", "temp")
    _snap()
    assert membership.clear_left(ids["star"], "test") is True
    after = _snap()
    assert abs(after[ids["star"]]["share"] - before[ids["star"]]["share"]) < 1e-12
    assert after[ids["star"]]["rank"] == before[ids["star"]]["rank"]


def test_set_left_is_idempotent(balances):
    mid = _member("v2-idem@x.com", 100.0)
    assert membership.set_left(mid, "test", "one") is True
    assert membership.set_left(mid, "test", "two") is False


# ── zero capital, degenerate rosters, held reads ─────────────────────────────

def test_zero_capital_means_zero_power_however_good_the_record(balances):
    """Structural, not filtered: score = capital × multiplier. Their ballot still
    counts at the tally's floor and their record is fully preserved."""
    rich = _member("v2-funded@x.com", 100.0)
    broke = _member("v2-broke@x.com", 0.0, accuracy=0.02, cycles=50)
    out = _snap()
    assert out[broke]["power"] == 0.0
    assert out[rich]["power"] == 100.0
    with db() as conn:
        assert conn.execute("SELECT accuracy FROM member_settings WHERE member_id=?",
                            (broke,)).fetchone()["accuracy"] == 0.02


def test_degenerate_roster_writes_nothing_and_carries_prior_power(balances):
    """Fail closed: a club with no committed capital must not zero anyone's vote."""
    mid = _member("v2-degen@x.com", 100.0)
    _snap()
    with db() as conn:
        conn.execute("UPDATE member_settings SET alloc_value=0 WHERE member_id=?", (mid,))
    assert reputation.snapshot_power_all() == []
    with db() as conn:
        assert conn.execute("SELECT voting_power FROM member_settings WHERE member_id=?",
                            (mid,)).fetchone()["voting_power"] == 100.0


def test_held_member_stays_in_the_denominator_and_is_not_written(balances, monkeypatch):
    """A transient broker error must be NEUTRAL. Dropping the member from the
    denominator inflated every other member's share while their stale dollar
    power kept voting."""
    a = _member("v2-held-a@x.com", 100.0)
    b = _member("v2-held-b@x.com", 100.0)
    before = _snap()
    assert before[a]["power"] == 100.0
    real = reputation.alloc.member_balance
    monkeypatch.setattr(reputation.alloc, "member_balance",
                        lambda mid: None if mid == a else real(mid))
    out = reputation.snapshot_power_all()
    ids = {o["member_id"] for o in out}
    assert a not in ids, "held member must not be written"
    assert {o["member_id"]: o["power"] for o in out}[b] == 100.0, (
        "held member's capital must stay in the pool")
    with db() as conn:
        assert conn.execute("SELECT voting_power FROM member_settings WHERE member_id=?",
                            (a,)).fetchone()["voting_power"] == 100.0


def test_pool_is_conserved(balances):
    ids = _roster(balances)
    out = _snap()
    with db() as conn:
        pool = sum(r["alloc_value"] for r in conn.execute(
            "SELECT alloc_value FROM member_settings").fetchall())
    assert abs(sum(o["power"] for o in out.values()) - pool) <= 0.01 * len(ids) + 0.01


# ── R1: inertia scales with membership ───────────────────────────────────────

def test_inertia_is_a_noop_at_the_anchor_and_slows_above_it(monkeypatch):
    _cfg(monkeypatch, accuracy_inertia_ref_n=24)
    assert reputation._inertia_ratio(24) == 1.0
    assert reputation._half_life_alpha_scaled(13, reputation._inertia_ratio(24)) == \
        reputation._half_life_alpha(13)
    prev = 1.0
    for n in (24, 48, 96, 240, 600):
        r = reputation._inertia_ratio(n)
        a = reputation._half_life_alpha_scaled(13, r)
        assert r >= prev
        prev = r
        assert a <= reputation._half_life_alpha(13)


def test_inertia_is_one_sided_a_shrinking_club_never_sharpens(monkeypatch):
    """A wave of departures must not suddenly make everyone's record volatile."""
    _cfg(monkeypatch, accuracy_inertia_ref_n=24)
    for n in (0, 1, 5, 23, 24):
        assert reputation._inertia_ratio(n) == 1.0


def test_inertia_can_be_disabled(monkeypatch):
    _cfg(monkeypatch, accuracy_inertia_ref_n=0)
    assert reputation._inertia_ratio(10_000) == 1.0


def test_r1_is_keyed_to_membership_not_turnout(balances, monkeypatch):
    """R1 must track how big the CLUB is. Keyed to per-cycle turnout it never
    fired once in the club's real 12-cycle history (turnout peaked at 21 vs a
    REF_N of 24) and would have made inertia depend on who showed up."""
    _cfg(monkeypatch, accuracy_inertia_ref_n=24)
    for i in range(30):
        _member(f"v2-r1-{i}@x.com", 100.0)
    assert reputation.active_member_count() == 30
    # 30 members but only 2 of them measurable in a cycle: inertia must still
    # reflect 30, not 2.
    assert reputation._inertia_ratio(reputation.active_member_count()) > 1.0
    assert reputation._inertia_ratio(2) == 1.0


def test_stamped_alphas_do_not_change_when_the_roster_changes(balances, monkeypatch):
    """N is read at SCORING time and frozen. If it were read at snapshot time an
    exit would change N and the exit rescale would stop being a single factor."""
    _cfg(monkeypatch, accuracy_inertia_ref_n=2)
    mids = [_member(f"v2-stamp{i}@x.com", 100.0) for i in range(4)]
    s1 = voting.open_session_for(datetime.date(2027, 9, 14))
    for m in mids:
        voting.set_member_picks(s1["id"], m, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 9, 16))
    for m in mids:
        voting.set_member_picks(s2["id"], m, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 110.0, "SPY": 404.0})
    reputation.capture_cycle_prices(s2["id"])
    reputation.score_matured_cycles()
    with db() as conn:
        rows = conn.execute("SELECT n_measured, alpha_short FROM cycle_scores "
                            "WHERE session_id=?", (s1["id"],)).fetchall()
    assert rows and {r["n_measured"] for r in rows} == {4}   # 4 measured voters
    stamped = {r["alpha_short"] for r in rows}
    assert len(stamped) == 1
    membership.set_left(mids[0], "test", "after-scoring")
    with db() as conn:
        again = {r["alpha_short"] for r in conn.execute(
            "SELECT alpha_short FROM cycle_scores WHERE session_id=?", (s1["id"],)).fetchall()}
    assert again == stamped, "a departure rewrote a frozen historical alpha"


# ── scoring integrity ────────────────────────────────────────────────────────

def test_winsorisation_bounds_a_freak_cycle(balances, monkeypatch):
    _cfg(monkeypatch, accuracy_excess_cap=0.05)
    mid = _member("v2-wins@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2027, 9, 21))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 9, 23))
    voting.set_member_picks(s2["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 200.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s2["id"])
    reputation.score_matured_cycles()
    with db() as conn:
        r = conn.execute("SELECT excess, excess_raw FROM cycle_scores WHERE member_id=?",
                         (mid,)).fetchone()
    assert abs(r["excess_raw"] - 1.0) < 1e-9, "raw +100% must be recorded verbatim"
    assert r["excess"] == pytest.approx(0.05), "the value that entered the EWMA must be clipped"


def test_scoring_applies_exactly_once(balances, monkeypatch):
    mid = _member("v2-once@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2027, 9, 28))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 9, 30))
    voting.set_member_picks(s2["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 110.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s2["id"])
    assert reputation.score_matured_cycles()
    with db() as conn:
        first = conn.execute("SELECT accuracy, cycles_participated FROM member_settings "
                             "WHERE member_id=?", (mid,)).fetchone()
    assert reputation.score_matured_cycles() == []
    with db() as conn:
        second = conn.execute("SELECT accuracy, cycles_participated FROM member_settings "
                              "WHERE member_id=?", (mid,)).fetchone()
    assert (first["accuracy"], first["cycles_participated"]) == \
           (second["accuracy"], second["cycles_participated"])


def test_an_unscoreable_cycle_is_not_sealed(balances, monkeypatch):
    """A cycle that could not be measured must stay retryable, not be stamped
    scored_at with zero effect and become permanently unmeasurable."""
    mid = _member("v2-unsealed@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2027, 10, 5))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 10, 7))
    # benchmark priced, but NOT the voted symbol → nothing measurable
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"SPY": 404.0})
    reputation.capture_cycle_prices(s2["id"])
    reputation.score_matured_cycles()
    with db() as conn:
        assert conn.execute("SELECT scored_at FROM vote_sessions WHERE id=?",
                            (s1["id"],)).fetchone()["scored_at"] is None


def test_departed_members_are_not_scored(balances, monkeypatch):
    stay = _member("v2-scored@x.com", 100.0)
    goer = _member("v2-unscored@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2027, 10, 12))
    for m in (stay, goer):
        voting.set_member_picks(s1["id"], m, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 10, 14))
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 110.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s2["id"])
    membership.set_left(goer, "test", "before-scoring")
    reputation.score_matured_cycles()
    with db() as conn:
        rows = {r["member_id"] for r in conn.execute("SELECT member_id FROM cycle_scores")}
    assert stay in rows and goer not in rows


def test_exit_prices_cover_the_previous_cycles_names(balances, monkeypatch):
    """Without this, a pick is measurable only if the SAME ticker is voted again
    next cycle — accuracy computed on a survivorship-biased subset."""
    mid = _member("v2-cover@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2027, 10, 19))
    voting.set_member_picks(s1["id"], mid, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {s: 100.0 for s in syms})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2027, 10, 21))
    voting.set_member_picks(s2["id"], mid, ["MSFT"])          # fully rotated out
    asked = {}

    def _q(syms):
        asked["syms"] = set(syms)
        return {s: 110.0 for s in syms}
    monkeypatch.setattr(reputation.market, "quotes", _q)
    reputation.capture_cycle_prices(s2["id"])
    assert "AAPL" in asked["syms"], "previous cycle's names must still be priced"


# ── config validation ────────────────────────────────────────────────────────

def test_invalid_power_config_is_refused(monkeypatch):
    assert invalid_power_config() == []
    for knob, bad in (("power_form", "nonsense"), ("merit_max_multiple", 0.5),
                      ("merit_accuracy_scale", 0.0), ("accuracy_excess_cap", 0.0),
                      ("accuracy_inertia_ref_n", -1), ("capital_gamma_bps", 20000)):
        with monkeypatch.context() as mp:
            _bind(mp, dataclasses.replace(_CUR[0], **{knob: bad}))
            assert invalid_power_config(), f"{knob}={bad} must be refused"


def test_gamma_above_one_is_refused(monkeypatch):
    """gamma > 1 AMPLIFIES large stakes — the opposite of the knob's purpose, and
    a one-character typo away from handing one member the whole club."""
    _cfg(monkeypatch, capital_gamma_bps=100000)
    assert any("CAPITAL_GAMMA_BPS" in p for p in invalid_power_config())


# ── sybil ────────────────────────────────────────────────────────────────────

def test_splitting_an_account_buys_nothing_at_gamma_one(balances):
    """At gamma=1 the score is linear in capital, so k accounts of c/k sum to
    exactly one account of c. (Below gamma=1 splitting PAYS k**(1-gamma) — the
    stated cost of dialling the whale knob.)"""
    other = _member("v2-sybil-other@x.com", 100.0)
    whole = _member("v2-sybil-whole@x.com", 1000.0)
    out = _snap()
    unsplit, other_before = out[whole]["power"], out[other]["power"]
    membership.set_left(whole, "test", "split")
    parts = [_member(f"v2-sybil-part{i}@x.com", 1000.0 / 4) for i in range(4)]
    out2 = _snap()
    assert abs(sum(out2[p]["power"] for p in parts) - unsplit) < 0.05
    assert abs(out2[other]["power"] - other_before) < 0.05


# ── regressions found by adversarial review (each reproduced before fixing) ──

def test_held_member_is_carried_on_the_very_first_snapshot(monkeypatch):
    """The first merit snapshot after migration has last_capital_usd=0 for
    everyone, so a held member had nothing to carry and was dropped from the
    denominator. Reproduced: a $1,000 member held on run one left a $100 member
    holding 100% of a live-money vote."""
    whale = _member("v2-first-whale@x.com", 1000.0)
    small = _member("v2-first-small@x.com", 100.0)
    # seed the fallback the migration provides, WITHOUT any prior snapshot
    with db() as conn:
        conn.execute("UPDATE member_settings SET voting_power=alloc_value")
    monkeypatch.setattr(reputation.alloc, "member_balance",
                        lambda mid: None if mid == whale else
                        {"total_value": 1e7, "buying_power": 1e7})
    out = {r["member_id"]: r for r in reputation.snapshot_power_all()}
    assert whale not in out, "held member must not be written"
    assert out[small]["share"] < 0.15, (
        f"held whale was dropped from the denominator — small got {out[small]['share']:.4f}")


def test_connecting_member_is_not_stranded_at_the_tally_floor(balances):
    """snapshot_member is the only writer on connect, and the club-wide snapshot
    is biweekly. Returning early under blend left a funded joiner at
    voting._tally_rows' MAX(voting_power, 1.0) floor for up to 13 days — a
    $2,000 member voting at 0.99% instead of ~95%."""
    from app.services import power as power_svc
    existing = _member("v2-strand-old@x.com", 100.0)
    _snap()
    newbie = _member("v2-strand-new@x.com", 2000.0)
    power_svc.snapshot_member(newbie)
    s = voting.open_session_for(datetime.date(2028, 3, 7))
    voting.set_member_picks(s["id"], newbie, ["AAPL"])
    voting.set_member_picks(s["id"], existing, ["MSFT"])
    w = {t["symbol"]: t["weight"] for t in voting.tally(s["id"])}   # no snapshot between
    assert w["AAPL"] > 1.0, "funded joiner stranded at the tally floor"
    assert w["AAPL"] > w["MSFT"], "a $2,000 member must outweigh a $100 member"


def test_connect_seeds_power_but_writes_no_history_row(balances):
    """The seed must not write a history row — that row's delta column is what
    rendered a phantom personal loss at the next real snapshot."""
    from app.services import power as power_svc
    mid = _member("v2-seed@x.com", 500.0)
    power_svc.snapshot_member(mid)
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM voting_power_history WHERE member_id=?",
                            (mid,)).fetchone()["c"] == 0
        r = conn.execute("SELECT voting_power, last_capital_usd FROM member_settings "
                         "WHERE member_id=?", (mid,)).fetchone()
    assert r["voting_power"] == 500.0 and r["last_capital_usd"] == 500.0


def test_merit_without_blend_is_refused(monkeypatch):
    """POWER_FORM only takes effect inside POWER_MODEL=blend. Setting merit with
    stake booted clean and silently ran legacy — failing in the direction of
    'I believe v2 is live'."""
    _bind(monkeypatch, dataclasses.replace(_CUR[0], power_form="merit", power_model="stake"))
    assert any("POWER_FORM=merit requires POWER_MODEL=blend" in p
               for p in invalid_power_config())


def test_bot_reweight_stands_down_under_merit(monkeypatch):
    """The legacy multiplicative bot reweight must not re-arm on a
    merit-without-blend misconfiguration — bots are the majority of tally weight."""
    from app.services import bots
    _bind(monkeypatch, dataclasses.replace(_CUR[0], power_form="merit", power_model="stake"))
    monkeypatch.setattr(bots, "settings", _CUR[0])
    res = bots.score_and_reweight()
    assert res["bots"] == [] and res["scored_from"] is None


def test_init_db_refuses_a_bad_knob(monkeypatch):
    """Every backend/jobs/ unit is its own process calling init_db() directly, so
    validating only in main.startup left ~16 entrypoints trading on a bad knob."""
    import app.db as dbmod
    _bind(monkeypatch, dataclasses.replace(_CUR[0], capital_gamma_bps=100000))
    with pytest.raises(RuntimeError, match="voting-power configuration"):
        dbmod.init_db()


def test_participation_is_frozen_while_a_basket_executes():
    """Opting out mid-execution was a free option: vote at full weight, watch the
    tally, opt out before the orders land, fund none of it — and still get scored,
    because accuracy reads `votes`, never `executions`."""
    import inspect

    from app.routes import members as members_route
    src = inspect.getsource(members_route.participate)
    assert "member_in_closed_unexecuted_session" in src
    assert "409" in src or "status_code=409" in src


def test_one_brokerage_account_cannot_back_two_members():
    """rh_accounts is keyed by member_id and account_number has no UNIQUE
    constraint, so without this guard ten signups could point at ONE $1,000
    account and each resolve $1,000 of committed capital — a 10x multiplication
    of one person's vote needing no second brokerage account."""
    from app.robinhood import vault
    a = _member("v2-acct-a@x.com", 100.0, connect=False)
    b = _member("v2-acct-b@x.com", 100.0, connect=False)
    vault.save_connection(a, {"access_token": "t"}, "RH-SHARED-1", True)
    with pytest.raises(vault.DuplicateAccount):
        vault.save_connection(b, {"access_token": "t"}, "RH-SHARED-1", True)
    # the same member reconnecting their OWN account is still fine
    vault.save_connection(a, {"access_token": "t2"}, "RH-SHARED-1", True)


def test_opted_out_member_stops_accruing_a_record(balances, monkeypatch):
    """Participation off means off: the ballot already cast still stands, but no
    accuracy is earned while out. Closes the free option — accuracy is measured
    from `votes`, the executor funds only opted_in=1, so a member could vote, opt
    out before the orders landed, fund nothing, and still be credited."""
    stays = _member("v2-opt-in@x.com", 100.0)
    leaves = _member("v2-opt-out@x.com", 100.0)
    s1 = voting.open_session_for(datetime.date(2028, 4, 4))
    for m in (stays, leaves):
        voting.set_member_picks(s1["id"], m, ["AAPL"])
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 100.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s1["id"])
    s2 = voting.open_session_for(datetime.date(2028, 4, 6))
    monkeypatch.setattr(reputation.market, "quotes", lambda syms: {"AAPL": 110.0, "SPY": 400.0})
    reputation.capture_cycle_prices(s2["id"])
    with db() as conn:
        conn.execute("UPDATE member_settings SET opted_in=0 WHERE member_id=?", (leaves,))
    reputation.score_matured_cycles()
    with db() as conn:
        scored = {r["member_id"] for r in conn.execute("SELECT member_id FROM cycle_scores")}
        acc = {r["member_id"]: r["accuracy"] for r in conn.execute(
            "SELECT member_id, accuracy FROM member_settings")}
    assert stays in scored and leaves not in scored
    assert acc[leaves] == 0.0, "opted-out member accrued a record"


def test_opting_out_does_not_reduce_voting_power(balances):
    """Owner's rule: opting out stops you voting, it does NOT take your weight
    away — you pick back up where you left off when you switch it on."""
    a = _member("v2-power-in@x.com", 100.0)
    b = _member("v2-power-out@x.com", 500.0)
    before = _snap()
    with db() as conn:
        conn.execute("UPDATE member_settings SET opted_in=0 WHERE member_id=?", (b,))
    after = _snap()
    assert after[b]["power"] == before[b]["power"]
    assert after[a]["power"] == before[a]["power"]


@pytest.mark.parametrize("n", [2, 50, 200])
def test_invariants_hold_at_scale(balances, n):
    """The exit property is algebraic, so member count cannot affect it — but
    pool conservation and the share-sum assert have absolute tolerances that
    must still hold as the club grows. Verified at 2, 50 and 200 members."""
    import random
    rnd = random.Random(11)
    ids = [_member(f"v2-scale{n}-{i}@x.com",
                   capital=round(max(1.0, rnd.lognormvariate(3.9, 1.1)), 2),
                   accuracy=rnd.gauss(0, 0.004) if i % 4 else 0.0,
                   cycles=rnd.randint(0, 40)) for i in range(n)]
    out = _snap()
    assert len(out) == n
    assert abs(sum(r["share"] for r in out.values()) - 1.0) < 1e-9
    committed = sum(r["capital"] for r in out.values())
    assert abs(sum(r["power"] for r in out.values()) - committed) <= 0.01 * n + 0.01
    if n < 3:
        return
    # one arbitrary leaver: ratios and ranks must survive exactly at any size
    before = {m: r["share"] for m, r in out.items()}
    leaver = ids[n // 3]
    membership.set_left(leaver, "test", "scale")
    after = {m: r["share"] for m, r in _snap().items()}
    f = 1.0 - before[leaver]
    assert max(abs(after[m] - before[m] / f) for m in after) < 1e-12
    surv = [m for m in ids if m != leaver]
    assert (sorted(surv, key=lambda m: -before[m])
            == sorted(surv, key=lambda m: -after[m]))


def test_share_ceiling_bounds_the_largest_member(balances, monkeypatch):
    """MAX_VOTING_SHARE pins anyone above the ceiling and redistributes the
    excess. Off by default; the club must be big enough for the cap to be
    satisfiable at all (shares sum to 1, so C needs C*N > 1)."""
    _cfg(monkeypatch, max_voting_share=0.0)
    ids = [_member(f"v2-cap{i}@x.com", 5000.0 if i == 0 else 200.0) for i in range(12)]
    uncapped = _snap()
    assert uncapped[ids[0]]["share"] > 0.20
    _cfg(monkeypatch, max_voting_share=0.20, max_voting_share_min_members=10)
    capped = _snap()
    assert capped[ids[0]]["share"] == pytest.approx(0.20, abs=1e-9)
    assert capped[ids[0]]["capped"] is True
    assert abs(sum(r["share"] for r in capped.values()) - 1.0) < 1e-9
    assert all(r["share"] <= 0.20 + 1e-9 for r in capped.values())


def test_share_ceiling_keeps_the_exit_guarantee_for_uncapped_members(balances, monkeypatch):
    """The whole reason water-filling is safe: the excess is redistributed by a
    COMMON factor, so any two members NOT at the ceiling keep their pairwise
    ratio exactly through an exit. Only a pinned member moves — the definition
    of a cap, not a regression."""
    _cfg(monkeypatch, max_voting_share=0.20, max_voting_share_min_members=10)
    ids = [_member(f"v2-capx{i}@x.com", 5000.0 if i == 0 else 200.0 + 30 * i)
           for i in range(12)]
    snap0 = _snap()
    before = {m: r["share"] for m, r in snap0.items()}
    pinned0 = {m for m, r in snap0.items() if r["capped"]}
    assert pinned0, "the ceiling must actually bind for this test to mean anything"
    for leaver in ids[1:6]:
        membership.set_left(leaver, "test", "cap-exit")
        snap1 = _snap()
        after = {m: r["share"] for m, r in snap1.items()}
        pinned1 = {m for m, r in snap1.items() if r["capped"]}
        free = [m for m in after if m not in pinned0 and m not in pinned1]
        for a, b in itertools.combinations(free, 2):
            assert abs((after[a] / after[b]) / (before[a] / before[b]) - 1) < 1e-9
        membership.clear_left(leaver, "test")


def test_share_ceiling_refuses_when_it_would_flatten_the_club(balances, monkeypatch):
    """A ceiling of C is unsatisfiable when C*N <= 1, and a C barely above 1/N
    pins nearly everyone. Both must leave shares untouched rather than silently
    collapsing the club to one-member-one-vote."""
    _cfg(monkeypatch, max_voting_share=0.20, max_voting_share_min_members=0)
    ids = [_member(f"v2-few{i}@x.com", 1000.0 if i == 0 else 100.0) for i in range(4)]
    out = _snap()
    assert not any(r["capped"] for r in out.values()), (
        "4 members is below the derived ceil(2/0.20)=10 threshold — must not apply")
    assert out[ids[0]]["share"] > 0.20


def test_share_ceiling_threshold_derives_from_the_ceiling(monkeypatch):
    """MAX_VOTING_SHARE_MIN_MEMBERS=0 derives ceil(2/C) rather than demanding a
    second number: 10% -> 20 members, 5% -> 40, 2% -> 100."""
    for C, expect in ((0.10, 20), (0.05, 40), (0.02, 100)):
        assert math.ceil(2.0 / C) == expect


def test_impossible_ceiling_pair_is_refused_at_startup(monkeypatch):
    """2% cannot be satisfied in a 50-member club. Refuse to boot rather than
    pin every member."""
    _bind(monkeypatch, dataclasses.replace(
        _CUR[0], max_voting_share=0.02, max_voting_share_min_members=50))
    assert any("unsatisfiable" in p for p in invalid_power_config())


@pytest.mark.parametrize("n", [2, 50, 200])
def test_invariants_hold_at_scale(balances, n):
    """The exit property is algebraic, so member count cannot affect it — but
    pool conservation and the share-sum assert have absolute tolerances that
    must still hold as the club grows. Verified at 2, 50 and 200 members."""
    import random
    rnd = random.Random(11)
    ids = [_member(f"v2-scale{n}-{i}@x.com",
                   capital=round(max(1.0, rnd.lognormvariate(3.9, 1.1)), 2),
                   accuracy=rnd.gauss(0, 0.004) if i % 4 else 0.0,
                   cycles=rnd.randint(0, 40)) for i in range(n)]
    out = _snap()
    assert len(out) == n
    assert abs(sum(r["share"] for r in out.values()) - 1.0) < 1e-9
    committed = sum(r["capital"] for r in out.values())
    assert abs(sum(r["power"] for r in out.values()) - committed) <= 0.01 * n + 0.01
    if n < 3:
        return
    # one arbitrary leaver: ratios and ranks must survive exactly at any size
    before = {m: r["share"] for m, r in out.items()}
    leaver = ids[n // 3]
    membership.set_left(leaver, "test", "scale")
    after = {m: r["share"] for m, r in _snap().items()}
    f = 1.0 - before[leaver]
    assert max(abs(after[m] - before[m] / f) for m in after) < 1e-12
    surv = [m for m in ids if m != leaver]
    assert (sorted(surv, key=lambda m: -before[m])
            == sorted(surv, key=lambda m: -after[m]))
