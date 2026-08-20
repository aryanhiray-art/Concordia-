"""The 'weekly' (production) cadence: ONE cycle a week, traded Monday.

Monday runs both legs in one session (limited-margin members complete same-day
via the executor's buying-power poll; cash members' remainders defer), Tuesday
is the deferred-cash top-up, Wed–Fri are idle. A Monday holiday shifts the pair
to Tue/Wed. Power redistribution is biweekly: every other Sunday from the
anchor.

Run from backend/:  python -m pytest tests/test_weekly_cadence.py -q
"""
import datetime as dt
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.calendar_us import cycle_phase, is_power_day, next_sell_day  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _weekly():
    prev = (settings.cycle_cadence, settings.cycle_start_date,
            settings.power_cadence, settings.power_anchor)
    object.__setattr__(settings, "cycle_cadence", "weekly")
    object.__setattr__(settings, "cycle_start_date", "2026-07-22")
    object.__setattr__(settings, "power_cadence", "biweekly")
    object.__setattr__(settings, "power_anchor", "2026-08-23")
    yield
    object.__setattr__(settings, "cycle_cadence", prev[0])
    object.__setattr__(settings, "cycle_start_date", prev[1])
    object.__setattr__(settings, "power_cadence", prev[2])
    object.__setattr__(settings, "power_anchor", prev[3])


def test_week_pattern_monday_both_tuesday_topup():
    # week of Mon 2026-08-24 — no holidays
    assert cycle_phase(dt.date(2026, 8, 24)) == "both"   # Mon: vote + both legs
    assert cycle_phase(dt.date(2026, 8, 25)) == "buy"    # Tue: deferred-cash top-up
    assert cycle_phase(dt.date(2026, 8, 26)) is None     # Wed
    assert cycle_phase(dt.date(2026, 8, 27)) is None     # Thu
    assert cycle_phase(dt.date(2026, 8, 28)) is None     # Fri
    assert cycle_phase(dt.date(2026, 8, 29)) is None     # Sat
    assert cycle_phase(dt.date(2026, 8, 30)) is None     # Sun


def test_monday_holiday_shifts_pair_to_tue_wed():
    # Labor Day 2026-09-07 is a NYSE closure → Tue trades, Wed tops up
    assert cycle_phase(dt.date(2026, 9, 7)) is None
    assert cycle_phase(dt.date(2026, 9, 8)) == "both"
    assert cycle_phase(dt.date(2026, 9, 9)) == "buy"
    assert cycle_phase(dt.date(2026, 9, 10)) is None


def test_ballot_targets_next_monday():
    # from any idle day, the next vote/sell day is the coming Monday
    assert next_sell_day(dt.date(2026, 8, 20)) == dt.date(2026, 8, 24)   # Thu →
    assert next_sell_day(dt.date(2026, 8, 21)) == dt.date(2026, 8, 24)   # Fri →
    assert next_sell_day(dt.date(2026, 8, 24)) == dt.date(2026, 8, 31)   # Mon → next Mon


def test_biweekly_power_every_other_sunday_from_anchor():
    assert is_power_day(dt.date(2026, 8, 23)) is True     # the anchor Sunday
    assert is_power_day(dt.date(2026, 8, 30)) is False    # off week
    assert is_power_day(dt.date(2026, 9, 6)) is True
    assert is_power_day(dt.date(2026, 9, 13)) is False
    assert is_power_day(dt.date(2026, 9, 20)) is True


def test_biweekly_bad_anchor_fails_open_to_weekly():
    object.__setattr__(settings, "power_anchor", "not-a-date")
    assert is_power_day(dt.date(2026, 8, 30)) is True     # safe failure = weekly
