"""Tests for the Series-A fund reward formulas (equations 2 and 3).

Pure functions, no DB — services/fund_rewards.py isn't wired to live data yet
(no pooled fund exists in this codebase today), so these tests validate the
math directly: partition-of-unity properties, floors, and the worked examples
from the business-plan appendix.

Run from backend/:  python -m pytest tests/test_fund_rewards.py -q
"""
import pytest

from app.services.fund_rewards import (
    MemberAccuracy,
    member_payout,
    national_reward_shares,
    reward_pool,
    royalty_amounts,
    royalty_pool,
    top_percentile_members,
)


# ── equation 2: reward pool + payout ──────────────────────────────────────────

def test_reward_pool_worked_example():
    """$1B AUM, fee tied at the 0.45% center → $2.0M pool (matches the business
    plan's own worked table: Concordia keeps $2.5M regardless)."""
    assert reward_pool(aum=1_000_000_000, fee_rate=0.0045) == pytest.approx(2_000_000)


def test_reward_pool_zero_at_or_below_floor():
    """A fund that lags to the floor (or worse) never owes the pool money."""
    assert reward_pool(aum=1_000_000_000, fee_rate=0.0025) == 0.0
    assert reward_pool(aum=1_000_000_000, fee_rate=0.0010) == 0.0   # can't go negative


def test_national_reward_shares_partition_of_unity():
    members = [MemberAccuracy(1, 0.02), MemberAccuracy(2, 0.01), MemberAccuracy(3, -0.05)]
    shares = national_reward_shares(members)
    assert shares[1] == pytest.approx(2 / 3)
    assert shares[2] == pytest.approx(1 / 3)
    assert shares[3] == 0.0                        # negative accuracy floors to zero
    assert sum(shares.values()) == pytest.approx(1.0)


def test_national_reward_shares_all_non_positive_pays_nobody():
    members = [MemberAccuracy(1, -0.01), MemberAccuracy(2, 0.0)]
    shares = national_reward_shares(members)
    assert shares == {1: 0.0, 2: 0.0}


def test_member_payout_combines_principal_growth_and_bonus():
    # $10,000 principal, fund up 8%, this member earned 10% of the reward
    # pool, pool is $2M → payout = 10,800 + 200,000
    assert member_payout(principal=10_000, r_fund=0.08, reward_share=0.10,
                         pool=2_000_000) == pytest.approx(210_800.0)


def test_member_payout_zero_share_still_gets_fund_return():
    """A member who didn't vote (or was wrong) is never worse off than the
    fund's own return — they just don't share the bonus pool."""
    assert member_payout(principal=10_000, r_fund=0.05, reward_share=0.0,
                         pool=2_000_000) == pytest.approx(10_500.0)


# ── equation 3: royalty on Concordia's own fee ────────────────────────────────

def test_top_percentile_members_uses_min_n_floor_when_network_is_small():
    members = [MemberAccuracy(i, 1.0 / i) for i in range(1, 21)]  # 20 members, all positive
    top = top_percentile_members(members, pct=0.01, min_n=10)     # 1% of 20 rounds to 0
    assert len(top) == 10
    assert top[0].member_id == 1                                  # highest accuracy first


def test_top_percentile_members_scales_with_network():
    members = [MemberAccuracy(i, 1.0 / i) for i in range(1, 10_001)]  # 10,000 members
    top = top_percentile_members(members, pct=0.01, min_n=10)
    assert len(top) == 100                                         # 1% of 10,000, not the floor


def test_top_percentile_members_excludes_non_positive_accuracy():
    members = [MemberAccuracy(1, 0.05), MemberAccuracy(2, -0.01), MemberAccuracy(3, 0.0)]
    top = top_percentile_members(members, pct=1.0, min_n=1)
    assert [m.member_id for m in top] == [1]


def test_royalty_pool_worked_example():
    # $2.5M Concordia revenue, 5% royalty rate → $125K royalty pool
    assert royalty_pool(concordia_revenue=2_500_000, royalty_rate=0.05) == pytest.approx(125_000)


def test_royalty_amounts_partition_and_ranking():
    top = [MemberAccuracy(1, 0.10), MemberAccuracy(2, 0.05), MemberAccuracy(3, 0.05)]
    amounts = royalty_amounts(top, pool=100_000)
    assert amounts[1] == pytest.approx(50_000)     # 0.10 / 0.20
    assert amounts[2] == pytest.approx(25_000)
    assert amounts[3] == pytest.approx(25_000)
    assert sum(amounts.values()) == pytest.approx(100_000)   # pool fully distributed


def test_royalty_amounts_empty_top_pays_nothing():
    assert royalty_amounts([], pool=100_000) == {}
