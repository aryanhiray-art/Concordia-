"""Minimal US equity market-calendar gate.

Weekend + NYSE full-holiday awareness so the open/close/execute jobs never fire
on a non-trading day. The holiday set is hand-maintained — EXTEND IT ANNUALLY (or
swap for `pandas_market_calendars` if it lands in the venv). `calendar_covers()`
+ the executor's pre-trade guard (finding #17) make a lapsed table fail loudly
(refuse to place real orders) rather than silently trading on a closed day.
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
    # 2028 (Jan 1 falls on a Saturday → not observed as a weekday closure)
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
    # 2029
    "2029-01-01", "2029-01-15", "2029-02-19", "2029-03-30", "2029-05-28",
    "2029-06-19", "2029-07-04", "2029-09-03", "2029-11-22", "2029-12-25",
    # 2030
    "2030-01-01", "2030-01-21", "2030-02-18", "2030-04-19", "2030-05-27",
    "2030-06-19", "2030-07-04", "2030-09-02", "2030-11-28", "2030-12-25",
}

# Last year the hand-maintained table is authoritative for. Beyond this we can't
# know the holidays, so the executor refuses to place REAL orders (finding #17).
_MAX_HOLIDAY_YEAR = max(int(h[:4]) for h in _HOLIDAYS)


def now_et() -> _dt.datetime:
    return _dt.datetime.now(ET)


def today_et() -> _dt.date:
    return now_et().date()


def calendar_covers(d: _dt.date | None = None) -> bool:
    """Is the holiday table authoritative for this date's year? (finding #17)."""
    return (d or today_et()).year <= _MAX_HOLIDAY_YEAR


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


def prev_trading_day(d: _dt.date | None = None) -> _dt.date:
    d = (d or today_et()) - _dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= _dt.timedelta(days=1)
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
        # One cycle a week, traded on MONDAY (or the week's first trading day
        # when Monday is a holiday). Same per-member split the mwf cadence
        # proved out: the vote day runs BOTH legs in one session — limited-
        # margin members complete same-day via unsettled-proceeds buying power
        # (executor polls for the credit), cash members' remainders defer —
        # and the NEXT trading day (Tuesday) is the top-up where deferred
        # cash buys land with settled proceeds. Wed–Fri are idle.
        first = _first_trading_day_of_week(d)
        if d == first:
            return "both"
        if d == next_trading_day(first):
            return "buy"
        return None
    if cad == "mwf":
        # Mon/Wed/Fri = vote days that run BOTH legs in one 9:31 session.
        # The per-member split falls out of the broker's own buying-power
        # semantics: limited-margin members' buying power includes today's
        # unsettled sale proceeds, so their buys complete same-day; cash
        # members deploy only already-settled cash and the executor defers
        # the rest. Tue/Thu (and any non-MWF trading day, e.g. after a
        # Monday holiday) = 'buy' top-up days where those deferred cash
        # buys land with now-settled proceeds; fully-deployed margin
        # members simply have nothing to top up and hold. A vote day lost
        # to a holiday is skipped (the next MWF picks up the cycle).
        return "both" if d.weekday() in (0, 2, 4) else "buy"
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
    if settings.power_cadence == "biweekly":
        # Every OTHER Sunday, anchored so the schedule is deterministic. A bad
        # anchor falls back to weekly (more frequent = the safe failure mode).
        try:
            y, m, dd = (int(x) for x in settings.power_anchor.split("-"))
            return ((d - _dt.date(y, m, dd)).days // 7) % 2 == 0
        except Exception:  # noqa: BLE001
            return True
    return True             # weekly
