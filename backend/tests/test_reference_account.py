"""The Alpaca reference account: planning, the TWR series, and the arming gates.

The reference account exists because members' Robinhood accounts are commingled
and Robinhood does not echo our order tag back, so the club's slice there has to
be reconstructed by netting an order feed. In one operator-owned Alpaca account
holding only the club's basket there is nothing to reconstruct.

The property these tests care about most is that DEPLOYING this code cannot
place an order. Three settings gates plus a funded budget must all be true.
"""
import os
import tempfile

os.environ["CONCORD_DB_PATH"] = tempfile.mkstemp(suffix=".db")[1]
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")

import time  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.services import reference  # noqa: E402


def setup_module(_):
    init_db()


def _set(**kw):
    for k, v in kw.items():
        object.__setattr__(settings, k, v)   # conftest restores after each test


# ─── arming gates ────────────────────────────────────────────────────────────

def test_defaults_cannot_place_an_order():
    """Shipping the code must never arm it."""
    assert settings.reference_will_place is False


def test_every_gate_is_required():
    _set(alpaca_key_id="k", alpaca_secret="s", reference_enabled=True,
         reference_armed=True, reference_budget_usd=1000.0,
         demo_mode=False, app_env="prod")
    assert settings.reference_will_place is True
    for off in ("reference_enabled", "reference_armed"):
        _set(**{off: False})
        assert settings.reference_will_place is False, f"{off} must block placement"
        _set(**{off: True})
    _set(reference_budget_usd=0.0)
    assert settings.reference_will_place is False, "an unfunded account must not trade"
    _set(reference_budget_usd=1000.0)
    _set(alpaca_secret="")
    assert settings.reference_will_place is False


def test_beta_and_demo_can_never_place():
    _set(alpaca_key_id="k", alpaca_secret="s", reference_enabled=True,
         reference_armed=True, reference_budget_usd=1000.0)
    _set(app_env="beta")
    assert settings.reference_will_place is False, "beta must never reach a real broker"
    _set(app_env="prod", demo_mode=True)
    assert settings.reference_will_place is False


def test_run_without_a_key_places_nothing():
    _set(alpaca_key_id="", alpaca_secret="")
    s = reference.run("2026-09-01", ["AAPL", "MSFT"])
    assert s["will_place"] is False and s["orders"] == []
    assert "no Alpaca key pair" in s["note"]


# ─── planning ────────────────────────────────────────────────────────────────

def test_plan_equal_weights_a_fresh_basket():
    orders = reference.plan(["AAPL", "MSFT", "NVDA"], 3000.0, held={})
    assert {o["symbol"] for o in orders} == {"AAPL", "MSFT", "NVDA"}
    assert all(o["side"] == "buy" and o["notional"] == 1000.0 for o in orders)


def test_plan_sells_names_that_left_the_basket_in_full():
    orders = reference.plan(["AAPL"], 1000.0, held={"AAPL": 1000.0, "TSLA": 250.0})
    exits = [o for o in orders if o["symbol"] == "TSLA"]
    assert exits == [{"symbol": "TSLA", "side": "sell", "notional": 250.0,
                      "note": "exited basket"}]
    assert not [o for o in orders if o["symbol"] == "AAPL"], "already at target"


def test_plan_skips_sub_dollar_drift():
    """Below Alpaca's notional minimum, and churning cents only pays spread."""
    assert reference.plan(["AAPL"], 1000.0, held={"AAPL": 999.50}) == []


def test_plan_tops_up_and_trims_toward_target():
    orders = {o["symbol"]: o for o in
              reference.plan(["AAPL", "MSFT"], 1000.0, held={"AAPL": 100.0, "MSFT": 900.0})}
    assert orders["AAPL"]["side"] == "buy" and orders["AAPL"]["notional"] == 400.0
    assert orders["MSFT"]["side"] == "sell" and orders["MSFT"]["notional"] == 400.0


def test_order_tag_is_deterministic_and_attributable():
    a = reference.order_tag("2026-09-01", "AAPL", "buy")
    assert a == reference.order_tag("2026-09-01", "aapl", "buy")   # case-insensitive
    assert a != reference.order_tag("2026-09-01", "AAPL", "sell")
    assert a != reference.order_tag("2026-09-02", "AAPL", "buy")
    assert a.startswith("concordia-") and len(a) <= 128


# ─── the series ──────────────────────────────────────────────────────────────

def _nav(d, equity, net_flow=0.0, spy=None):
    with db() as conn:
        conn.execute("INSERT INTO reference_nav(d,equity,net_flow,spy_close,created_at) "
                     "VALUES(?,?,?,?,?) ON CONFLICT(d) DO UPDATE SET equity=excluded.equity,"
                     "net_flow=excluded.net_flow, spy_close=excluded.spy_close",
                     (d, equity, net_flow, spy, int(time.time())))


def test_series_is_empty_before_any_snapshot():
    s = reference.series()
    assert s["points"] == [] and s["club_pct"] is None
    assert s["basis"] == "reference_account"


def test_a_deposit_is_not_a_return():
    """The defect clubnav approximates with a 25% jump threshold. Here the flow
    is a real number from the activities feed, so it is removed exactly."""
    _nav("2026-09-01", 1000.0, spy=100.0)
    _nav("2026-09-02", 2000.0, net_flow=1000.0, spy=100.0)   # funded, market flat
    s = reference.series()
    assert s["club_pct"] == 0.0, "adding cash must not read as performance"

    _nav("2026-09-03", 2100.0, net_flow=0.0, spy=100.0)      # +5% on 2000
    assert reference.series()["club_pct"] == 5.0


def test_benchmark_indexes_off_the_first_close():
    _nav("2026-09-01", 1000.0, spy=100.0)
    _nav("2026-09-02", 2000.0, net_flow=1000.0, spy=100.0)
    _nav("2026-09-03", 2100.0, spy=110.0)
    s = reference.series()
    assert s["spy_pct"] == 10.0
    assert s["start_date"] == "2026-09-01"
