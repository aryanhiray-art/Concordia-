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


def _first_trading_day_of_week(d: _dt.date) -> _dt.date:
    cur = d - _dt.timedelta(days=d.weekday())  # Monday of d's ISO week
    for _ in range(7):
        if is_trading_day(cur):
            return cur
        cur += _dt.timedelta(days=1)
    return d


def cycle_phase(d: _dt.date | None = None) -> str | None:
    """What the club does on this date — a cycle is SPLIT across two trading days
    so a cash account's T+1 settlement is respected:

      'sell' = vote + sell the names that dropped from the new basket (keep the
               ones that stayed); proceeds settle overnight.
      'buy'  = buy the new basket with the now-settled proceeds (the next day).
      'both' = sell + buy same day (daily cadence; ignores settlement — testing).
      None   = idle (no trading today).
    """
    from .config import settings
    d = d or today_et()
    if not is_trading_day(d):
        return None
    # cadence is dormant before the start date (e.g. the day before is a one-off
    # fresh-start liquidation, not a normal cycle day)
    try:
        sy, sm, sd = (int(x) for x in settings.cycle_start_date.split("-"))
        if d < _dt.date(sy, sm, sd):
            return None
    except Exception:  # noqa: BLE001
        pass
    cad = settings.cycle_cadence
    if cad == "daily":
        return "both"
    if cad == "weekly":
        first = _first_trading_day_of_week(d)
        if d == first:
            return "sell"
        if d == next_trading_day(first):
            return "buy"
        return None
    # 'alternate' (fast testing): even trading-day = sell, odd = buy. Every
    # trading day is active — Mon sell → settles → Tue buy → Wed sell → Thu buy.
    return "sell" if _trading_index(d) % 2 == 0 else "buy"


def is_sell_day(d: _dt.date | None = None) -> bool:
    return cycle_phase(d) in ("sell", "both")


def is_buy_day(d: _dt.date | None = None) -> bool:
    return cycle_phase(d) in ("buy", "both")


def next_sell_day(d: _dt.date | None = None) -> _dt.date:
    """The next vote/sell day strictly after d (where a new basket is decided)."""
    nd = next_trading_day(d or today_et())
    for _ in range(180):
        if cycle_phase(nd) in ("sell", "both"):
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
