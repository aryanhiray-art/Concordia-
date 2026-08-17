"""The 'mwf' cadence: Mon/Wed/Fri vote days run both legs; Tue/Thu top up.

Margin members finish same-day (their broker buying power includes unsettled
proceeds); cash members' deferred buys land on the next trading day with
settled cash. These tests pin the calendar layer: which phase each date gets,
weekend/holiday behavior, and that ballot scheduling (is_sell_day /
next_sell_day) follows the vote days.

Run from backend/:  python -m pytest tests/test_mwf_cadence.py -q
"""
import datetime as dt
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.calendar_us import cycle_phase, is_sell_day, next_sell_day  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _mwf():
    # settings is a frozen import-time singleton — the repo's idiom (see
    # conftest.py) is to mutate it in place with object.__setattr__ and
    # restore afterward, keeping this module order-independent in the suite.
    prev = (settings.cycle_cadence, settings.cycle_start_date)
    object.__setattr__(settings, "cycle_cadence", "mwf")
    object.__setattr__(settings, "cycle_start_date", "2026-07-22")
    yield
    object.__setattr__(settings, "cycle_cadence", prev[0])
    object.__setattr__(settings, "cycle_start_date", prev[1])


def test_week_pattern_mwf_both_tue_thu_buy():
    # 2026-08-17 is a Monday with no market holidays that week.
    assert cycle_phase(dt.date(2026, 8, 17)) == "both"   # Mon — vote + both legs
    assert cycle_phase(dt.date(2026, 8, 18)) == "buy"    # Tue — cash top-up
    assert cycle_phase(dt.date(2026, 8, 19)) == "both"   # Wed
    assert cycle_phase(dt.date(2026, 8, 20)) == "buy"    # Thu
    assert cycle_phase(dt.date(2026, 8, 21)) == "both"   # Fri
    assert cycle_phase(dt.date(2026, 8, 22)) is None     # Sat — idle
    assert cycle_phase(dt.date(2026, 8, 23)) is None     # Sun — idle
    assert cycle_phase(dt.date(2026, 8, 24)) == "both"   # Mon again


def test_holiday_vote_day_is_skipped_next_mwf_picks_up():
    # Labor Day 2026 is Monday Sep 7 — a lost vote day, not shifted.
    assert cycle_phase(dt.date(2026, 9, 7)) is None       # holiday — idle
    assert cycle_phase(dt.date(2026, 9, 8)) == "buy"      # Tue — top-up only
    assert cycle_phase(dt.date(2026, 9, 9)) == "both"     # Wed — next vote day


def test_ballot_scheduling_follows_vote_days():
    # Sell/vote days are exactly the 'both' days.
    assert is_sell_day(dt.date(2026, 8, 19)) is True
    assert is_sell_day(dt.date(2026, 8, 20)) is False
    # From Monday, the next vote day strictly after is Wednesday.
    assert next_sell_day(dt.date(2026, 8, 17)) == dt.date(2026, 8, 19)
    # Across the weekend: Friday's next vote day is Monday.
    assert next_sell_day(dt.date(2026, 8, 21)) == dt.date(2026, 8, 24)


def test_dormant_before_start_date():
    assert cycle_phase(dt.date(2026, 7, 20)) is None  # Mon before CYCLE_START_DATE
