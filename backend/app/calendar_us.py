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


# ─── cadence: how often the club re-votes/trades + redistributes power ────────
# A "cycle day" is a trading day the club actually votes + rebalances on. With a
# cash account (T+1 settlement), trading every day is impossible, so the default
# is alternate trading days (a settlement day always sits between cycles).

def _anchor() -> _dt.date:
    from .config import settings  # lazy: avoid import cycle at module load
    try:
        y, m, d = (int(x) for x in settings.cycle_anchor.split("-"))
        return _dt.date(y, m, d)
    except Exception:  # noqa: BLE001
        return _dt.date(2026, 6, 22)


def _trading_index(d: _dt.date) -> int:
    """Count of trading days between the anchor and d (parity is what matters)."""
    a = _anchor()
    lo, hi = (a, d) if d >= a else (d, a)
    n, cur = 0, lo
    while cur < hi:
        cur = next_trading_day(cur)
        n += 1
    return n


def _is_first_trading_day_of_week(d: _dt.date) -> bool:
    cur = d - _dt.timedelta(days=d.weekday())  # Monday of d's ISO week
    for _ in range(7):
        if is_trading_day(cur):
            return cur == d
        cur += _dt.timedelta(days=1)
    return False


def is_cycle_day(d: _dt.date | None = None) -> bool:
    """Does the club vote + rebalance on this date, per the configured cadence?"""
    from .config import settings
    d = d or today_et()
    if not is_trading_day(d):
        return False
    cad = settings.cycle_cadence
    if cad == "weekly":
        return _is_first_trading_day_of_week(d)
    if cad == "alternate":
        return _trading_index(d) % 2 == 0
    return True  # daily


def next_cycle_day(d: _dt.date | None = None) -> _dt.date:
    """The next cycle day strictly after d."""
    nd = next_trading_day(d or today_et())
    for _ in range(180):
        if is_cycle_day(nd):
            return nd
        nd = next_trading_day(nd)
    return nd


def is_power_day(d: _dt.date | None = None) -> bool:
    """Should voting power redistribute on this date (the power timers fire weekly)?"""
    from .config import settings
    d = d or today_et()
    if settings.power_cadence == "monthly":
        return d.day <= 7   # the first occurrence of this weekday in the month
    return True             # weekly
