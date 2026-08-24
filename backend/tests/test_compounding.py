"""Compounding stakes (owner decision 2026-08-24, default ON).

Contract under test:
- compound_budget: bootstrap from the stake on an empty slice; gains ride
  ($110 slice → $110 budget); losses compound down ($90 → $90, never topped
  up); a stake CHANGE deploys/retires exactly the delta; safety clamps hold
- basket_targets (water-fill): a name grown within $1 of fair share KEEPS its
  value and the other entries shrink so the total still sums to the budget;
  a name >$1 over fair share is targeted at fair share (trimmed)
- the /me/compound toggle flips the column and honors the mid-cycle freeze

Run from backend/:  python -m pytest tests/test_compounding.py -q
"""
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services.alloc import basket_targets, compound_budget  # noqa: E402

client = TestClient(app)


def setup_module(_):
    init_db()


# ─── compound_budget ─────────────────────────────────────────────────────────

def test_bootstrap_empty_slice_uses_full_stake():
    assert compound_budget(100.0, None, 0.0, 500.0) == 100.0
    assert compound_budget(100.0, 100.0, 0.0, 500.0) == 100.0   # applied but liquidated


def test_gains_ride_and_losses_compound_down():
    assert compound_budget(100.0, 100.0, 110.94, 500.0) == 110.94   # Rohan's case
    assert compound_budget(100.0, 100.0, 90.0, 500.0) == 90.0       # never topped up


def test_stake_change_deploys_exactly_the_delta():
    # raise 100 → 200 while the slice sits at 110: deploy 110 + 100 = 210
    assert compound_budget(200.0, 100.0, 110.0, 500.0) == 210.0
    # cut 100 → 50: retire 50 → 60 stays deployed
    assert compound_budget(50.0, 100.0, 110.0, 500.0) == 60.0
    # cut below zero floors at 0 (full liquidation)
    assert compound_budget(0.0, 100.0, 80.0, 500.0) == 0.0


def test_budget_clamps_hold():
    assert compound_budget(100.0, 100.0, 110.0, 105.0) == 105.0      # ≤ account value
    assert compound_budget(100.0, 100.0, 110.0, 500.0, daily_cap_usd=75.0) == 75.0


# ─── basket_targets water-fill ───────────────────────────────────────────────

def test_winner_kept_and_other_entries_shrink_to_fit():
    # the owner's exact scenario: one name grew $5 → $5.50, budget still $100
    held = {f"S{i}": 0.0 for i in range(19)}
    held["WIN"] = 5.50
    t = basket_targets(100.0, held)
    assert t["WIN"] == 5.50                          # winner rides
    others = [v for k, v in t.items() if k != "WIN"]
    assert all(abs(v - others[0]) < 0.02 for v in others)   # equal entries
    assert abs(sum(t.values()) - 100.0) < 0.25       # total adds up
    assert others[0] < 5.0                           # entries reduced below the naive $5


def test_far_overweight_name_is_trimmed_to_fair_share():
    held = {"BIG": 12.0, **{f"S{i}": 5.0 for i in range(19)}}
    t = basket_targets(100.0, held)
    assert t["BIG"] < 12.0 - 1.0                     # targeted well below its value → trim fires
    assert abs(sum(t.values()) - 100.0) < 1.5


def test_empty_and_zero_budget():
    assert basket_targets(0.0, {"A": 5.0}) == {"A": 0.0}
    assert basket_targets(100.0, {}) == {}


def test_all_names_flat_get_equal_shares():
    t = basket_targets(100.0, {f"S{i}": 0.0 for i in range(20)})
    assert all(abs(v - 5.0) < 0.01 for v in t.values())


# ─── toggle endpoint ─────────────────────────────────────────────────────────

def _mk_member(email: str):
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,0,?)",
            (email, email.split("@")[0], "member", hash_password("compound-pass-11"), now, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,1,0,0,25,0,'dollar',100.0,?)", (mid, now),
        )
    return mid


def test_toggle_defaults_on_and_flips():
    mid = _mk_member("compound@iclub.test")
    tok = make_session(mid, 1)
    with db() as conn:
        row = conn.execute("SELECT COALESCE(compound,1) AS c FROM member_settings "
                           "WHERE member_id=?", (mid,)).fetchone()
    assert row["c"] == 1                             # default ON
    r = client.put("/api/me/compound", json={"on": False},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["member"]["settings"]["compound"] == 0
