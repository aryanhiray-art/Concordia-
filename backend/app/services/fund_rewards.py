"""Concordia Fund reward economics — the Series-A end-state mechanism (business
plan §12, "the fund's signature" fulcrum fee). NOT yet wired to live data: this
codebase has no pooled fund, no AUM ledger, and no per-member principal ledger
today — that infrastructure ships with the introducing-broker-dealer / '40 Act
fund build-out. These are the pure, unit-tested formulas the fund's semiannual
settlement job will call once that infrastructure exists.

Three equations power Concordia's economics; only the first is live:
  1. Voting power              → services/reputation.py (live today)
  2. Member payout              → member_payout() / national_reward_shares()
  3. Royalty (top performers)   → royalty_amounts() / top_percentile_members()

(2) and (3) both key off the SAME blended accuracy score that already drives
voting power in reputation.py — "whose vote counts more" and "who gets paid
more" are one underlying metric, not two separately-tuned systems.

Settlement cadence: every 6 months. `fee_rate` (the fulcrum fee itself) is
still measured on the fund's rolling TWELVE-month excess return against the
benchmark — that keeps the Fidelity Magellan / AB FlexFee regulatory precedent
the business plan cites intact — but reward_pool() is evaluated and paid out
twice a year against that trailing-12-month number.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemberAccuracy:
    member_id: int
    accuracy: float   # the blended (short+long EWMA) score from reputation.py


# ─── equation 2: reward pool + member payout ─────────────────────────────────

def reward_pool(aum: float, fee_rate: float, floor: float = 0.0025) -> float:
    """The $ funding the reward pool: whatever the fulcrum fee charges above
    Concordia's flat floor. Floored at 0 — a lagging fund (fee_rate <= floor)
    never owes members money out of Concordia's own guaranteed cut."""
    return max(0.0, aum) * max(0.0, fee_rate - floor)


def national_reward_shares(members: list[MemberAccuracy]) -> dict[int, float]:
    """Each member's slice of the reward pool, summing to 1 across every member
    with POSITIVE accuracy, ranked network-wide (not per-club — the fund pools
    the whole club network's research per §12: "clubs become the fund's
    research engine"). Non-positive accuracy floors to share 0, never negative."""
    positive = {m.member_id: m.accuracy for m in members if m.accuracy > 0}
    total = sum(positive.values())
    shares = {mid: (acc / total if total > 0 else 0.0) for mid, acc in positive.items()}
    for m in members:
        shares.setdefault(m.member_id, 0.0)
    return shares


def member_payout(principal: float, r_fund: float, reward_share: float, pool: float) -> float:
    """principal grown at the fund's own pro-rata return, PLUS a cut of the
    reward pool proportional to how right this member's votes were. A member
    with reward_share=0 (new, inactive, or wrong) still gets their fund return
    in full — nobody is worse off for sitting cycles out, they just don't
    share the bonus pool that their votes didn't help fund."""
    return principal * (1.0 + r_fund) + reward_share * pool


# ─── equation 3: royalty on Concordia's own fee ──────────────────────────────

def top_percentile_members(members: list[MemberAccuracy], pct: float = 0.01,
                           min_n: int = 10) -> list[MemberAccuracy]:
    """The top `pct` of members by accuracy, network-wide. `min_n` keeps the
    leaderboard real while the network is small (pct of 200 members would
    round to 2 people; a percentile-only cut looks empty long before it looks
    exclusive). Ties broken by member_id — deterministic, never insertion
    order (so a settlement run is reproducible from the same DB state)."""
    eligible = [m for m in members if m.accuracy > 0]
    if not eligible:
        return []
    n = max(min_n, round(len(eligible) * pct))
    ranked = sorted(eligible, key=lambda m: (-m.accuracy, m.member_id))
    return ranked[:n]


def royalty_pool(concordia_revenue: float, royalty_rate: float) -> float:
    """A small slice of Concordia's OWN flat-floor revenue, paid out as a
    marketing/retention expense — never drawn from the members' reward_pool()."""
    return max(0.0, concordia_revenue) * max(0.0, royalty_rate)


def royalty_amounts(top_members: list[MemberAccuracy], pool: float) -> dict[int, float]:
    """Split `pool` across `top_members` proportional to accuracy within that
    top slice (not their share of the whole network — a top-100th-percentile
    member should split the leaderboard pool against other leaderboard
    members, not get diluted by everyone outside it). Empty input pays out
    nothing rather than dividing by zero."""
    total = sum(m.accuracy for m in top_members)
    if total <= 0:
        return {}
    return {m.member_id: (m.accuracy / total) * pool for m in top_members}
