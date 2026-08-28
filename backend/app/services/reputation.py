"""Reputation & blended voting power. Two score forms, selected by POWER_FORM.

MERIT (v2) — a single product, normalised ONCE:

    score(i) = capital(i)**gamma · M(i)
    M(i)     = MERIT_MAX_MULTIPLE ** tanh( accuracy(i) / MERIT_ACCURACY_SCALE )
    VP(i)    = P · score(i) / Σ score(j)          where P = Σ capital(j)

  In one sentence for a member: your voting power is the money you committed,
  multiplied by a track-record score, expressed as your share of the club's
  total committed dollars.

  Three properties follow, and they are the reason the form changed:

  1. AN EXIT IS A PURE RENORMALISATION. M(i) reads ONLY member i's own accuracy —
     no sum, no mean, no cohort, no member count. So removing member k changes no
     numerator and only removes score(k) from the denominator:
         share'(i) = share(i) / (1 − share(k))     for EVERY survivor
     One common factor, so every pairwise ratio and the whole rank order survive
     exactly. Nobody is reset; the pool is simply re-divided. Joining is the
     mirror. This is why nothing cross-sectional may ever enter M().
  2. NO ZERO-CLIFF. tanh is strictly increasing through zero, so "unproven" and
     "proven bad" are finally different numbers. Under the old max(acc,0) they
     were the same number: on the live roster a member 1bp underwater, one 70x
     worse, and one with no record at all all landed on identical power.
  3. BOUNDED COMPRESSION. tanh saturates, so M ∈ [1/MM, MM] and best:worst can
     never exceed MM² — an a-priori bound, not an observed one. MM is the single
     spread dial; lowering it brings members closer together without making them
     equal and without re-ranking anyone. accuracy 0 ⇒ M == 1.0 exactly, so an
     unscored club weighs exactly its dollars AT gamma == 1 (the default) and
     enabling the form is a no-op. Below gamma 1 the shares are c**gamma
     normalised, so cold start is proportional to stake but not equal to it.

LEGACY — the additive form, kept as the default and as the rollback:

    raw(i) = CAPITAL·CapitalShare(i) + ACCURACY·[AccuracyShare(i)·confidence(i)]

  It normalises capital and accuracy SEPARATELY and adds them, which is what
  breaks property 1: an exit inflates the capital channel by 1/(1−C_k) and the
  accuracy channel by 1/(1−A_k) — two different factors — so survivors are
  re-weighted according to where their score came from. Measured on the live
  roster, one departure drifts pairwise ratios by up to 24.7% and can flip ranks
  between two members who did nothing. It also multiplies accuracy share by
  tenure, double-counting newness (accuracy already ramps from zero) and
  producing rank inversions where a longer-serving member with a worse record
  outranks a better one.

Accuracy is scored one cycle after each vote from prices captured at close
(paper basis — you're credited for what you BACKED, even picks that lost the
vote), winsorised per cycle, and smoothed by a two-timescale EWMA whose
half-lives lengthen as the club grows (see _inertia_ratio).

`gamma` (config.capital_gamma_bps, default 1.0 = linear) dials capital toward
sqrt-style diminishing returns. NOTE it is sybil-UNSAFE below 1.0: splitting one
stake across k accounts multiplies its weight by k**(1-gamma), so gamma<1 needs
identity assurance before it is enabled.

Enabled by POWER_MODEL=blend; dormant (prices still captured) otherwise. See the
hackathon repo cx18121/concordia — DESIGN.md §3, Governance.sol, scoring.ts.
"""
from __future__ import annotations

import math
import time

from ..config import settings
from ..db import audit, db
from . import alloc, market, membership
from . import power as power_svc


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── price capture (at session close) ─────────────────────────────────────────

def capture_cycle_prices(session_id: int) -> dict:
    """Snapshot the price of every VOTED symbol + the benchmark at close — the
    'entry' a cycle's picks are scored against. Runs regardless of POWER_MODEL so
    the history is already there whenever blend is switched on. Best-effort."""
    with db() as conn:
        syms = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM votes WHERE session_id=?", (session_id,)).fetchall()]
        # ALSO price the PREVIOUS cycle's voted symbols. Scoring uses this
        # session's prices as the EXIT for the previous one, so capturing only
        # today's names means a pick is measurable solely when the same ticker
        # was voted again — accuracy was being computed on a survivorship-biased
        # subset (names that stayed popular), and a member whose picks all
        # rotated out registered as a NON-measurement instead of a result.
        prev = conn.execute(
            "SELECT id FROM vote_sessions WHERE trade_date < "
            "(SELECT trade_date FROM vote_sessions WHERE id=?) "
            "ORDER BY trade_date DESC LIMIT 1", (session_id,)).fetchone()
        if prev:
            syms += [r["symbol"] for r in conn.execute(
                "SELECT DISTINCT symbol FROM votes WHERE session_id=?", (prev["id"],)).fetchall()]
    want = sorted(set(s.upper() for s in syms) | {settings.benchmark_symbol})
    try:
        px = market.quotes(want)
    except Exception:  # noqa: BLE001
        px = {}
    now = int(time.time())
    n = 0
    with db() as conn:
        for s in want:
            p = px.get(s.upper())
            if p and p > 0:
                conn.execute(
                    "INSERT INTO cycle_prices(session_id,symbol,price,captured_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(session_id,symbol) DO UPDATE SET price=excluded.price, captured_at=excluded.captured_at",
                    (session_id, s.upper(), p, now))
                n += 1
    return {"session_id": session_id, "captured": n, "wanted": len(want)}


def _prices_for(session_id: int) -> dict[str, float]:
    with db() as conn:
        return {r["symbol"]: r["price"] for r in conn.execute(
            "SELECT symbol, price FROM cycle_prices WHERE session_id=?", (session_id,)).fetchall()}


# ── accuracy scoring (a cycle after the vote) ────────────────────────────────

def _half_life_alpha(half_life_cycles: int) -> float:
    """EWMA smoothing constant that halves a single cycle's weight after
    `half_life_cycles` further cycles: alpha solves (1-alpha)**H = 0.5."""
    h = max(1, half_life_cycles)
    return 1.0 - 2.0 ** (-1.0 / h)


def _half_life_alpha_scaled(half_life_cycles: int, ratio: float) -> float:
    """The same constant with the half-life stretched by `ratio` (>= 1.0).

    Goes through _half_life_alpha so (1-alpha)**H == 0.5 keeps holding exactly at
    the stretched half-life; ratio 1.0 is bit-identical to the unscaled alpha,
    which is what makes shipping R1 a no-op at the anchor roster size.
    """
    h = max(1.0, float(half_life_cycles) * max(1.0, ratio))
    return 1.0 - 2.0 ** (-1.0 / h)


def _alpha_short() -> float:
    return _half_life_alpha(settings.accuracy_short_half_life_cycles)


def _alpha_long() -> float:
    return _half_life_alpha(settings.accuracy_long_half_life_cycles)


def _blend_accuracy(short: float, long: float) -> float:
    w_short = settings.accuracy_short_weight_bps / 1e4
    w_long = settings.accuracy_long_weight_bps / 1e4
    return w_short * short + w_long * long


def active_member_count() -> int:
    """Members currently IN the club — the R1 input. Counted at scoring time."""
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM members m WHERE {membership.active_clause('m')}"
        ).fetchone()
    return int(row["n"] or 0)


def _inertia_ratio(n_members: int) -> float:
    """R1 — the club gets SLOWER as it grows: half-lives are multiplied by
    max(1, sqrt(N / REF_N)).

    N is the ACTIVE MEMBER COUNT, not the cycle's turnout. That distinction
    matters: keyed to turnout this never fired even once across the club's whole
    12-cycle history (turnout peaked at 21 against a REF_N of 24), and it would
    have made inertia depend on who happened to show up rather than on how big
    the club is — the opposite of "more members, steadier standings".

    Why sqrt and why one-sided. A single cycle is one noisy sample of a member's
    skill, and as the club grows each member's picks are a smaller fraction of
    the decided basket, so the per-cycle estimate gets noisier roughly like
    sqrt(N) — matching the averaging rate to it keeps the score's
    signal-to-noise roughly constant instead of letting a big club whipsaw on
    one week. max(1, ...) is one-sided on purpose: a SHRINKING club must never
    sharpen the score, otherwise a wave of departures would suddenly make
    everyone's record volatile.

    N is read HERE and stamped into cycle_scores forever. It must never be
    evaluated at snapshot time: an exit changes N, and an N-dependent term in
    the snapshot would destroy the single-factor rescale that R3 depends on.
    """
    ref = settings.accuracy_inertia_ref_n
    if ref <= 0 or n_members <= 0:
        return 1.0
    return max(1.0, math.sqrt(n_members / float(ref)))


def _winsorise(x: float) -> float:
    cap = abs(settings.accuracy_excess_cap)
    return max(-cap, min(cap, x))


def score_cycle(scored_session_id: int, exit_prices: dict[str, float],
                claim: bool = False) -> dict:
    """EWMA-update every voter's accuracy from session `scored`'s vote-weighted
    excess return vs the benchmark. entry = that session's captured prices,
    exit = `exit_prices`. Idempotency (scored_at) is the caller's responsibility."""
    entry = _prices_for(scored_session_id)
    bench = settings.benchmark_symbol
    be, bx = entry.get(bench), exit_prices.get(bench)
    if not be or not bx:
        # No benchmark price on one side → can't compute excess-vs-benchmark. SKIP the
        # whole cycle (caller must NOT set scored_at) so it retries with real data,
        # rather than silently scoring RAW return (sp_ret=0) and sealing it.
        return {"scored_session": scored_session_id, "scored": False,
                "reason": "missing_benchmark_price", "members": []}
    sp_ret = bx / be - 1.0
    with db() as conn:
        # DEPARTED members are excluded by identity: their ballot stays on record
        # but stops earning a reputation that no longer belongs to anyone.
        #
        # Deliberately NOT filtered on opted_in. Scoring happens a full cycle
        # AFTER the outcome is public, so reading the CURRENT switch would let a
        # member watch a cycle go against them and then opt out to delete it
        # from their record — under merit, accuracy is the entire merit input,
        # so that ratchets a score upward on a history containing only wins.
        # The vote-but-do-not-fund case it was meant to cover is closed at the
        # other end instead: voting.member_has_live_ballot blocks the opt-out
        # while the ballot the member voted in is still live (see routes/members).
        rows = conn.execute(
            "SELECT v.member_id, v.symbol, v.weight FROM votes v "
            "JOIN members m ON m.id = v.member_id "
            f"WHERE v.session_id=? AND {membership.active_clause('m')}",
            (scored_session_id,)).fetchall()
    by_member: dict[int, list[tuple[str, float]]] = {}
    for r in rows:
        by_member.setdefault(r["member_id"], []).append((r["symbol"].upper(), r["weight"] or 0.0))

    # PASS 1 — measure everyone before mutating anyone, so N (and the alphas
    # derived from it) is the same for every member in the cycle. Computing it
    # incrementally would make a member's smoothing depend on their position in
    # the loop.
    measured: dict[int, tuple[float, float, int]] = {}   # mid -> (excess, raw, picks)
    unmeasurable: list[int] = []
    for mid, picks in by_member.items():
        raw = 0.0
        used = 0
        for sym, w in picks:
            e, x = entry.get(sym), exit_prices.get(sym)
            if not e or not x or w <= 0:
                continue
            raw += (w / 100.0) * ((x / e - 1.0) - sp_ret)   # vote-weighted excess vs benchmark
            used += 1
        if used == 0:
            # Not a single pick had both an entry AND exit price → this is a NON-
            # measurement, not "the member scored 0". Do NOT decay their accuracy or
            # ramp their confidence on it.
            unmeasurable.append(mid)
            continue
        measured[mid] = (_winsorise(raw), raw, used)

    if not measured:
        # The benchmark priced but not one member had a measurable pick. Today this
        # was sealed with scored_at and could never be re-scored; leave it unscored
        # so a later run with better price coverage can still measure it.
        return {"scored_session": scored_session_id, "scored": False,
                "reason": "no_measurable_members", "members": []}

    n_measured = len(measured)
    # R1 is keyed to how big the CLUB is, not to this cycle's turnout.
    n_members = active_member_count()
    ratio = _inertia_ratio(n_members)
    a_short = _half_life_alpha_scaled(settings.accuracy_short_half_life_cycles, ratio)
    a_long = _half_life_alpha_scaled(settings.accuracy_long_half_life_cycles, ratio)
    vals = [v[0] for v in measured.values()]
    mean = sum(vals) / n_measured
    dispersion = (math.sqrt(sum((v - mean) ** 2 for v in vals) / (n_measured - 1))
                  if n_measured > 1 else None)

    # PASS 2 — apply. A REAL transaction (the connection is autocommit, so the
    # explicit BEGIN IMMEDIATE is required): a crash mid-loop must not leave half
    # the club scored against a cycle the caller has already stamped as done.
    now = int(time.time())
    scored = []
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if claim:
                # The claim lives INSIDE this transaction. Claiming in a separate
                # committed statement left two failure windows: a hard kill
                # between claim and apply sealed a cycle with nothing applied and
                # the driver query (WHERE scored_at IS NULL) never retried it,
                # and the compensating release could clear a claim whose EWMA had
                # already committed, applying a non-idempotent update twice.
                # One transaction makes both impossible.
                if conn.execute(
                        "UPDATE vote_sessions SET scored_at=? WHERE id=? AND scored_at IS NULL",
                        (now, scored_session_id)).rowcount != 1:
                    conn.execute("ROLLBACK")
                    return {"scored_session": scored_session_id, "scored": False,
                            "reason": "already_claimed", "members": []}
            for mid, (excess, raw, used) in measured.items():
                row = conn.execute(
                    "SELECT accuracy_short, accuracy_long FROM member_settings WHERE member_id=?", (mid,)
                ).fetchone()
                old_short = float(row["accuracy_short"]) if row and row["accuracy_short"] is not None else 0.0
                old_long = float(row["accuracy_long"]) if row and row["accuracy_long"] is not None else 0.0
                new_short = a_short * excess + (1 - a_short) * old_short
                new_long = a_long * excess + (1 - a_long) * old_long
                new = _blend_accuracy(new_short, new_long)
                conn.execute(
                    "UPDATE member_settings SET accuracy=?, accuracy_short=?, accuracy_long=?, "
                    "cycles_participated=cycles_participated+1, updated_at=? WHERE member_id=?",
                    (new, new_short, new_long, now, mid))
                conn.execute(
                    "INSERT INTO cycle_scores(session_id,member_id,excess,excess_raw,alpha_short,"
                    "alpha_long,n_measured,n_members,picks_scored,dispersion,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,member_id) DO NOTHING",
                    (scored_session_id, mid, excess, raw, a_short, a_long,
                     n_measured, n_members, used, dispersion, now))
                scored.append({"member_id": mid, "cycle_excess_pct": round(excess * 100, 3),
                               "cycle_excess_raw_pct": round(raw * 100, 3),
                               "winsorised": excess != raw,
                               "accuracy": round(new, 5), "accuracy_short": round(new_short, 5),
                               "accuracy_long": round(new_long, 5), "picks_scored": used})
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    for mid in unmeasurable:
        scored.append({"member_id": mid, "picks_scored": 0, "skipped": "no_measurable_picks"})
    return {"scored_session": scored_session_id, "scored": True,
            "benchmark_return_pct": round(sp_ret * 100, 3),
            "n_measured": n_measured, "n_members": n_members,
            "inertia_ratio": round(ratio, 4),
            "alpha_short": a_short, "alpha_long": a_long, "members": scored}


def score_matured_cycles() -> list[dict]:
    """Score every unscored session that has entry prices AND a later session with
    prices to serve as the exit (a clean one-cycle window). Idempotent via scored_at."""
    with db() as conn:
        sessions = [dict(r) for r in conn.execute(
            "SELECT id, trade_date FROM vote_sessions WHERE scored_at IS NULL "
            "AND id IN (SELECT DISTINCT session_id FROM cycle_prices) ORDER BY trade_date").fetchall()]
    out = []
    for s in sessions:
        with db() as conn:
            nxt = conn.execute(
                "SELECT id FROM vote_sessions WHERE trade_date > ? "
                "AND id IN (SELECT DISTINCT session_id FROM cycle_prices) ORDER BY trade_date LIMIT 1",
                (s["trade_date"],)).fetchone()
        if not nxt:
            continue   # not matured yet — no exit prices captured for a later cycle
        # score_cycle claims scored_at inside its OWN transaction, atomically with
        # the EWMA it applies. A cycle is therefore either fully scored and
        # sealed, or untouched and still retryable — never sealed-but-unapplied
        # (which the driver's `WHERE scored_at IS NULL` would never revisit) and
        # never applied twice. No compensating release is needed, so there is no
        # release path that can race a commit.
        res = score_cycle(s["id"], _prices_for(nxt["id"]), claim=True)
        if not res.get("scored"):
            continue
        out.append(res)
    if out:
        audit("system", "accuracy_scored", None, {"cycles": len(out)})
    return out


# ── blended power snapshot ───────────────────────────────────────────────────

def _member_capital(m: dict) -> float:
    """Committed stake ($) — the CapitalShare numerator. Bots hold no real money;
    while they're test stand-ins for humans they carry a FAKE stake (bot_stake_usd)
    so they vote as funded members. Set BOT_STAKE_USD=0 to make bots pure-accuracy."""
    if m["is_bot"]:
        return float(settings.bot_stake_usd)
    bal = alloc.member_balance(m["member_id"])
    if not bal:
        return 0.0
    return alloc.rebalance_budget(m["alloc_type"], m["alloc_value"],
                                  bal.get("total_value"), bal.get("buying_power")) or 0.0


def _member_equity(m: dict) -> float:
    """Real account value ($) for the history/graph row (bots = their fake stake)."""
    if m["is_bot"]:
        return float(settings.bot_stake_usd)
    bal = alloc.member_balance(m["member_id"])
    return ((bal.get("total_value") if bal else None) or 0.0)


def _member_snapshot_inputs(m: dict) -> tuple[float, float, bool]:
    """(capital, equity, ok) from ONE balance fetch. ok=False means a CONNECTED
    member's live balance read FAILED (transient Robinhood error) — the caller must
    SKIP the write and carry the member's prior power, never persist a 0 from a
    failed read (that would collapse a funded member's vote for a whole week)."""
    if m["is_bot"]:
        fs = float(settings.bot_stake_usd)
        return fs, fs, True
    if (m.get("rh_status") or "") != "connected":
        return 0.0, 0.0, True   # legitimately uncommitted — no connected account, genuine 0
    bal = alloc.member_balance(m["member_id"])
    if not bal:
        return 0.0, 0.0, False  # connected but the read FAILED — do NOT zero their power
    cap = alloc.rebalance_budget(m["alloc_type"], m["alloc_value"],
                                 bal.get("total_value"), bal.get("buying_power")) or 0.0
    return cap, (bal.get("total_value") or 0.0), True


def merit_multiplier(accuracy: float) -> float:
    """M = MERIT_MAX_MULTIPLE ** tanh(accuracy / MERIT_ACCURACY_SCALE).

    The whole of R2 in one line, with properties that hold a priori rather than
    being observed on a particular roster:
      * bounded: tanh in (-1,1) so M in (1/MM, MM); overflow is impossible and
        best:worst can never exceed MM**2, whatever anyone's accuracy is;
      * neutral: accuracy 0 gives M == 1.0 EXACTLY. At gamma == 1 (the default)
        that makes cold start equal raw stake to the cent, so switching the form
        on an unscored club is a no-op; at gamma < 1 the capital term is
        c**gamma, so cold start stays proportional but is no longer equal;
      * no cliff: strictly increasing through zero, so an unproven member and a
        proven-bad member are finally different numbers. Under the old
        max(acc,0) they were identical — Chip Charlie at -0.00010, Mega-Cap Max
        at -0.00716 (seventy times worse) and Neil with no record at all all
        landed on exactly the same power;
      * SELF-CONTAINED: reads only this member's own accuracy. No sum, no mean,
        no cohort, no member count. That is the entire proof of the exit
        property below — anything cross-sectional here would break it.
    """
    mm = settings.merit_max_multiple
    scale = settings.merit_accuracy_scale
    if mm <= 1.0 or scale <= 0:
        return 1.0
    return mm ** math.tanh(accuracy / scale)


def _apply_share_ceiling(shares: dict[int, float]) -> tuple[dict[int, float], set[int]]:
    """Cap any member above MAX_VOTING_SHARE, redistributing the excess.

    Water-fill: pin everyone over the ceiling, hand their excess to the members
    below it in proportion to what they already hold, repeat until nobody is
    over (each pass pins at least one member, so it terminates).

    Because the redistribution is a COMMON multiplicative factor, any two
    members who are NOT pinned keep their pairwise ratio exactly — the exit
    guarantee survives for everyone the ceiling does not touch. Only a member
    actually sitting at the ceiling moves relative to the rest, which is what a
    ceiling is for.

    OFF unless both knobs are set, and refuses to act when it would be
    destructive rather than protective:
      * below MAX_VOTING_SHARE_MIN_MEMBERS the club is too small to bother;
      * a ceiling C is unsatisfiable when C * N <= 1 (shares sum to 1), and a C
        barely above 1/N pins nearly everyone and silently turns the club into
        one-member-one-vote. Both cases leave shares untouched and audit.
    """
    ceiling = settings.max_voting_share
    if not ceiling or ceiling <= 0 or not shares:
        return shares, set()
    # Count members who actually BEAR share, not raw headcount. A member with
    # zero committed capital scores 0 and holds 0 share, so they can neither be
    # capped nor absorb anyone else's excess — counting them made both guards
    # pass on a roster the ceiling could not actually be satisfied on. With 8
    # funded members and 52 signed-up-but-unconnected ones a 10% ceiling looked
    # feasible (0.10 * 60 = 6.0 > 1) while the real bearers could only ever
    # reach 0.10 * 8 = 0.8, and the water-fill returned a vector summing to 0.8
    # that aborted the ENTIRE snapshot every run.
    n = sum(1 for v in shares.values() if v > 0)
    # Threshold derives from the ceiling when unset: below ceil(2/C) members a
    # cap of C pins a large fraction of the club rather than an outlier.
    floor_n = settings.max_voting_share_min_members or math.ceil(2.0 / ceiling)
    if n < floor_n:
        return shares, set()
    if ceiling * n <= 1.0:
        audit("system", "voting_ceiling_infeasible", None,
              {"ceiling": ceiling, "members": n,
               "needs_members_above": int(1.0 / ceiling)})
        return shares, set()
    out = dict(shares)
    pinned: set[int] = set()
    for _ in range(n + 1):
        over = [m for m in out if m not in pinned and out[m] > ceiling + 1e-15]
        if not over:
            break
        for m in over:
            out[m] = ceiling
            pinned.add(m)
        rest = [m for m in out if m not in pinned]
        base = sum(out[m] for m in rest)
        if not rest or base <= 0:
            # Nobody left who can absorb the excess: the ceiling is not
            # satisfiable on this roster after all. Returning here would leave
            # shares summing to less than 1, which the pre-write assertion
            # correctly rejects — aborting the whole snapshot and freezing the
            # club's power indefinitely. Abandon the CEILING instead, which
            # degrades to "uncapped" rather than to "no snapshot at all".
            audit("system", "voting_ceiling_infeasible", None,
                  {"ceiling": ceiling, "members": n,
                   "reason": "no unpinned member could absorb the excess"})
            return shares, set()
        excess = 1.0 - sum(out.values())
        for m in rest:
            out[m] += excess * out[m] / base
    if pinned:
        audit("system", "voting_ceiling_applied", None,
              {"ceiling": ceiling, "members": n, "pinned": sorted(pinned)})
    return out, pinned


def snapshot_power_all(dry_run: bool = False) -> list[dict]:
    """Blended VP for every ACTIVE member, rescaled to the $ capital pool.

    Two score forms (POWER_FORM), one normalisation and one writer:

      merit   score_i = capital_i**gamma * M(accuracy_i)         [v2, default off]
      legacy  score_i = cap_w*capShare_i + acc_w*accShare_i*conf_i

    `merit` is a single product normalised once. Because score_i depends only on
    member i's own data, removing member k divides every survivor's share by the
    SAME factor (1 - share_k): pairwise ratios and rank order are preserved
    exactly, and an exit becomes one club-level pool change instead of 23
    individual losses. `legacy` normalises capital and accuracy separately and
    adds them, so an exit re-weights survivors by where their score came from
    (measured on the live roster: ratios drift up to 24.7%, and ranks can flip).
    """
    with db() as conn:
        members = [dict(r) for r in conn.execute(
            "SELECT m.id AS member_id, COALESCE(m.is_bot,0) AS is_bot, s.alloc_type, s.alloc_value, "
            "       COALESCE(s.accuracy,0) AS accuracy, COALESCE(s.cycles_participated,0) AS cycles_participated, "
            "       COALESCE(s.last_capital_usd,0) AS last_capital_usd, "
            "       COALESCE(s.voting_power,0) AS voting_power, "
            "       a.status AS rh_status "
            "FROM members m JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id "
            f"WHERE {membership.active_clause('m')}").fetchall()]
    inputs = {m["member_id"]: _member_snapshot_inputs(m) for m in members}

    # A member whose live balance read FAILED keeps their last known capital and
    # STAYS in the denominator, but is not written. Dropping them out of the
    # denominator (the old behaviour) inflated every other member's share by
    # 1/(1-share_held) while the held member's own stale dollar power kept
    # voting — a transient broker error silently re-weighted the whole club.
    roster: list[dict] = []      # everyone counted in the denominator
    writable: list[dict] = []    # everyone whose fresh read succeeded
    held: list[int] = []
    caps: dict[int, float] = {}
    eqs: dict[int, float] = {}
    for m in members:
        mid = m["member_id"]
        cap, eq, ok = inputs[mid]
        if ok:
            caps[mid] = max(0.0, cap)
            eqs[mid] = eq
            roster.append(m)
            writable.append(m)
        else:
            held.append(mid)
            # Fall back through EVERY record of this member's committed capital
            # before giving up on them. last_capital_usd is only populated from
            # the first successful snapshot, so on the very first run after the
            # migration it is 0 for everyone — and gating the carry on it alone
            # meant one transient Robinhood error dropped the largest member out
            # of the denominator entirely. (Reproduced: a $1,000 member held on
            # the first snapshot left a $100 member holding 100% of a live vote.)
            # Only a member whose capital has NEVER been observed is dropped.
            last = max(0.0, float(m["last_capital_usd"] or 0.0)) or \
                max(0.0, float(m["voting_power"] or 0.0))
            if last > 0:
                caps[mid] = last
                roster.append(m)

    total_cap = sum(caps.values())   # the $ pool VP rescales to — LINEAR regardless of gamma
    gamma = settings.capital_gamma_bps / 1e4
    merit = settings.power_form == "merit"

    score: dict[int, float] = {}
    if merit:
        for m in roster:
            mid = m["member_id"]
            c = caps[mid]
            score[mid] = (c ** gamma) * merit_multiplier(float(m["accuracy"])) if c > 0 else 0.0
    else:
        cap_w = settings.capital_bps / 1e4
        acc_w = settings.accuracy_bps / 1e4
        conf_n = max(1, settings.confidence_cycles)
        total_pos_acc = sum(max(0.0, float(m["accuracy"])) for m in roster)
        cap_weight = {m["member_id"]: (caps[m["member_id"]] ** gamma
                                       if caps[m["member_id"]] > 0 else 0.0) for m in roster}
        total_cap_weight = sum(cap_weight.values())
        for m in roster:
            mid = m["member_id"]
            cap_share = (cap_weight[mid] / total_cap_weight) if total_cap_weight > 0 else 0.0
            acc_share = (max(0.0, float(m["accuracy"])) / total_pos_acc) if total_pos_acc > 0 else 0.0
            conf = min(float(m["cycles_participated"]) / conf_n, 1.0)
            score[mid] = cap_w * cap_share + acc_w * acc_share * conf
    total_score = sum(score.values())

    # FAIL CLOSED. A degenerate roster must not zero anyone's vote: write nothing
    # and carry every member's prior power. Never refuses a lifecycle transition —
    # an empty club simply produces an empty snapshot.
    degenerate = (not roster or total_cap <= 0 or total_score <= 0
                  or not math.isfinite(total_score) or not math.isfinite(total_cap))
    if degenerate:
        audit("system", "power_snapshot_degenerate", power_svc.week_start_et(),
              {"roster": len(roster), "total_cap": total_cap, "total_score": total_score,
               "held": held, "form": settings.power_form})
        return []

    week = power_svc.week_start_et()
    now = int(time.time())
    shares = {mid: score[mid] / total_score for mid in score}
    shares, pinned = _apply_share_ceiling(shares)
    vps = {mid: round(total_cap * shares[mid], 2) for mid in shares}
    ranked = sorted(shares, key=lambda mid: (-shares[mid], mid))
    rank_of = {mid: i + 1 for i, mid in enumerate(ranked)}

    # Pre-write assertions. Any failure aborts the WHOLE snapshot rather than
    # leaving the club half-re-weighted.
    ssum = sum(shares.values())
    if abs(ssum - 1.0) > 1e-9 or any(not math.isfinite(v) or v < 0 for v in shares.values()):
        audit("system", "power_snapshot_degenerate", week, {"reason": "share_sum", "sum": ssum})
        return []
    # Pool conservation: the dollars handed out must be the dollars committed,
    # up to one cent of rounding per member.
    if abs(sum(vps.values()) - total_cap) > 0.01 * len(roster) + 0.01:
        audit("system", "power_snapshot_degenerate", week,
              {"reason": "pool_conservation", "sum_vp": sum(vps.values()), "pool_usd": total_cap})
        return []

    # Previous powers in ONE query, BEFORE the transaction opens. power_svc.
    # _prev_power opens its own connection per call (db() connects per call and
    # re-runs three PRAGMAs), and calling it inside the loop meant N fresh
    # connections while this one holds a write lock — safe under WAL, but
    # needless, and it made the transaction's boundary depend on another
    # module's connection handling.
    prev_power: dict[int, float] = {}
    with db() as conn:
        for r in conn.execute(
            "SELECT member_id, power FROM voting_power_history WHERE (member_id, week_start) IN "
            "(SELECT member_id, MAX(week_start) FROM voting_power_history "
            " WHERE week_start < ? GROUP BY member_id)", (week,)).fetchall():
            prev_power[r["member_id"]] = float(r["power"])

    out = []
    if dry_run:
        # COMPUTE ONLY — no history row, no member_settings write, no audit.
        # This is what makes the pre-flip check in RUNBOOK section 8 possible:
        # an operator can see what POWER_FORM=merit would produce while the club
        # is still running legacy.
        return [{"member_id": m["member_id"], "is_bot": bool(m["is_bot"]),
                 "capital": round(caps[m["member_id"]], 2),
                 "accuracy": round(float(m["accuracy"]), 5),
                 "merit": round(merit_multiplier(float(m["accuracy"])), 4),
                 "share": shares[m["member_id"]], "rank": rank_of[m["member_id"]],
                 "capped": m["member_id"] in pinned,
                 "power": vps[m["member_id"]],
                 "delta": (vps[m["member_id"]] - prev_power[m["member_id"]])
                          if m["member_id"] in prev_power else None}
                for m in writable]
    # ONE transaction for the whole club. The connection is autocommit
    # (isolation_level=None), so this needs an EXPLICIT BEGIN IMMEDIATE — without
    # it every execute() commits on its own and a crash halfway through leaves
    # half the club weighted on the new denominator and half on the old, under
    # the same week_start key, which is then what the next execution trades.
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for m in writable:
                mid = m["member_id"]
                vp = vps[mid]
                prev = prev_power.get(mid)
                delta = (vp - prev) if prev is not None else None
                conn.execute(
                    "INSERT INTO voting_power_history(member_id,week_start,power,equity,prev_power,"
                    "delta,created_at,share,pool_usd,n_active,rank_at_snapshot) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(member_id,week_start) DO UPDATE SET "
                    "power=excluded.power, equity=excluded.equity, prev_power=excluded.prev_power, "
                    "delta=excluded.delta, share=excluded.share, pool_usd=excluded.pool_usd, "
                    "n_active=excluded.n_active, rank_at_snapshot=excluded.rank_at_snapshot",
                    (mid, week, vp, round(eqs[mid], 2), prev, delta, now,
                     shares[mid], round(total_cap, 2), len(roster), rank_of[mid]))
                conn.execute(
                    "UPDATE member_settings SET voting_power=?, voting_power_share=?, "
                    "last_capital_usd=?, updated_at=? WHERE member_id=?",
                    (vp, shares[mid], caps[mid], now, mid))
                out.append({"member_id": mid, "is_bot": bool(m["is_bot"]),
                            "capital": round(caps[mid], 2),
                            "accuracy": round(float(m["accuracy"]), 5),
                            "merit": round(merit_multiplier(float(m["accuracy"])), 4),
                            "confidence": round(min(float(m["cycles_participated"])
                                                    / max(1, settings.confidence_cycles), 1.0), 3),
                            "share": shares[mid], "rank": rank_of[mid],
                        "capped": mid in pinned,
                            "power": vp, "delta": delta})
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    audit("system", "blended_power", week,
          {"members": len(out), "form": settings.power_form, "roster": len(roster),
           "pool_usd": round(total_cap, 2), "held_read_fail": held,
           "capped": sorted(pinned),
           "merit_min": round(min((o["merit"] for o in out), default=1.0), 4),
           "merit_max": round(max((o["merit"] for o in out), default=1.0), 4)})
    return out


def preview_forms() -> dict:
    """What each score form WOULD produce right now, writing nothing.

    Both branches run against the same live balances, so the comparison is
    apples-to-apples. Used by GET /api/admin/power-preview so the flip can be
    inspected before it happens rather than discovered afterwards.
    """
    import dataclasses

    from .. import config as _cfg
    base = settings
    out = {}
    for form in ("legacy", "merit"):
        patched = dataclasses.replace(base, power_form=form)
        globals()["settings"] = patched
        _cfg.settings = patched
        try:
            out[form] = {r["member_id"]: r for r in snapshot_power_all(dry_run=True)}
        finally:
            globals()["settings"] = base
            _cfg.settings = base
    rows = []
    for mid, leg in out["legacy"].items():
        mer = out["merit"].get(mid)
        if not mer:
            continue
        rows.append({
            "member_id": mid, "is_bot": leg["is_bot"], "capital": leg["capital"],
            "accuracy": leg["accuracy"], "merit": mer["merit"],
            "legacy_power": leg["power"], "merit_power": mer["power"],
            "change_pct": (round((mer["power"] / leg["power"] - 1) * 100, 2)
                           if leg["power"] else None),
            "legacy_rank": leg["rank"], "merit_rank": mer["rank"],
            "capped": mer["capped"],
        })
    rows.sort(key=lambda r: -r["merit_power"])
    return {"form_in_effect": base.power_form, "members": rows,
            "biggest_gain_pct": max((r["change_pct"] for r in rows
                                     if r["change_pct"] is not None), default=None),
            "biggest_loss_pct": min((r["change_pct"] for r in rows
                                     if r["change_pct"] is not None), default=None),
            "rank_changes": sum(1 for r in rows if r["legacy_rank"] != r["merit_rank"])}


def resolve_and_snapshot() -> dict:
    """POWER_MODEL=blend entrypoint: score any matured cycles, then re-snapshot
    blended voting power for everyone (humans + bots, one formula)."""
    scored = score_matured_cycles()
    snaps = snapshot_power_all()
    return {"mode": "blend", "scored_cycles": len(scored), "snapshots": len(snaps),
            "scored": scored, "power": snaps}
