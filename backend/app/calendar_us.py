"""Minimal US equity market-calendar gate.

Weekend + NYSE full-holiday awareness so the open/close/execute jobs never fire
on a non-trading day. Holiday set covers 2026–2027; extend annually (or swap for
`pandas_market_calendars` if it lands in the venv).
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE full-day closures (YYYY-MM-DD). Half-days still trade (regular orders ok).
_HOLIDAYS: set[str] = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def now_et() -> _dt.datetime:
    return _dt.datetime.now(ET)


def today_et() -> _dt.date:
    return now_et().date()


def is_trading_day(d: _dt.date | None = None) -> bool:
    d = d or today_et()
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d.isoformat() not in _HOLIDAYS


def next_trading_day(d: _dt.date | None = None) -> _dt.date:
    d = (d or today_et()) + _dt.timedelta(days=1)
    while not is_trading_day(d):
        d += _dt.timedelta(days=1)
    return d


def at_et(d: _dt.date, hhmm: str) -> _dt.datetime:
    """Combine a date with an `HH:MM` Eastern wall-clock time → aware datetime."""
    h, m = (int(x) for x in hhmm.split(":"))
    return _dt.datetime(d.year, d.month, d.day, h, m, tzinfo=ET)
