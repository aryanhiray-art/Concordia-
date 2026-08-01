"""Each chart range must be REAL data at its own interval.

Before this, services/stocks.py made exactly one call hardcoded to
interval="day" over a year and sliced 1M/3M/1Y out of it — so "1D" could not
exist (a day of daily bars is one point) and the day-range strip read the last
DAILY bar, which pre-open or at a weekend is the PREVIOUS session shown as
today's. These tests pin the properties that fix cost:

  * 1D/1W are genuinely intraday — consecutive stamps MINUTES apart, not days
  * 1D covers exactly one session
  * each range spans roughly its own window
  * the day strip says WHICH session it drew (is_today), never implying today
  * flipping ranges cannot re-hit the broker inside the TTL

Run from backend/:  python -m pytest tests/test_stock_ranges.py -q
"""
import os
import tempfile
import time
from datetime import datetime

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.calendar_us import ET  # noqa: E402
from app.db import init_db  # noqa: E402
from app.robinhood.demo import DemoClient  # noqa: E402
from app.services import stocks  # noqa: E402


def setup_module(_):
    init_db()


@pytest.fixture(autouse=True)
def _demo_client(monkeypatch):
    """Serve every range from the deterministic demo broker, and count calls."""
    calls = {"n": 0}
    real = DemoClient.get_equity_historicals

    def counted(self, symbols, start_time, interval="day", bounds=None, end_time=None):
        calls["n"] += 1
        return real(self, symbols, start_time, interval=interval,
                    bounds=bounds, end_time=end_time)

    monkeypatch.setattr(DemoClient, "get_equity_historicals", counted)
    monkeypatch.setattr(stocks, "_client", lambda: DemoClient())
    stocks._cache.clear()
    yield calls
    stocks._cache.clear()


def _et(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ET)


@pytest.mark.parametrize("rng,max_gap_s", [("1D", 15 * 60), ("1W", 90 * 60)])
def test_intraday_ranges_are_minutes_apart_not_days(rng, max_gap_s):
    pts = stocks.history("AAPL", rng)["points"]
    assert len(pts) > 10, f"{rng} returned too few bars to be intraday: {len(pts)}"
    assert all("t" in p for p in pts), f"{rng} points carry no timestamp"
    gaps = [pts[i + 1]["t"] - pts[i]["t"] for i in range(len(pts) - 1)]
    within = [g for g in gaps if g <= max_gap_s]
    # Most steps are the bar interval; overnight gaps in 1W are legitimately larger.
    assert within, f"{rng} had no intraday-sized steps at all"
    assert min(within) < 86400, f"{rng} is daily data wearing an intraday label"


def test_1d_is_one_session():
    pts = stocks.history("AAPL", "1D")["points"]
    days = {_et(p["t"]).strftime("%Y-%m-%d") for p in pts}
    assert len(days) == 1, f"1D spanned {len(days)} sessions: {sorted(days)}"


def test_daily_ranges_span_roughly_their_window():
    for rng, days, slack in (("1M", 31, 12), ("3M", 92, 20), ("1Y", 366, 40)):
        pts = stocks.history("AAPL", rng)["points"]
        assert pts and all("d" in p for p in pts), f"{rng} is not daily"
        first = datetime.strptime(pts[0]["d"], "%Y-%m-%d")
        last = datetime.strptime(pts[-1]["d"], "%Y-%m-%d")
        span = (last - first).days
        assert days - slack - 20 <= span <= days + slack, \
            f"{rng} spanned {span}d, expected about {days}d"


def test_day_strip_reports_which_session_it_drew():
    day = stocks.history("AAPL", "1D")["day"]
    assert day["session_date"], "no session date — the UI cannot say which day this is"
    today = _et(time.time()).strftime("%Y-%m-%d")
    # The flag must MATCH reality, not merely exist: on a weekend the last
    # session is not today, and the UI relabels itself off exactly this.
    assert day["is_today"] == (day["session_date"] == today)
    assert day["low"] <= day["high"]


def test_flipping_ranges_does_not_re_hit_the_broker(_demo_client):
    for rng in ("1D", "1W", "1M"):
        stocks.history("AAPL", rng)
    first = _demo_client["n"]
    assert first == 3, f"expected one call per range, got {first}"
    for _ in range(3):
        for rng in ("1D", "1W", "1M"):
            stocks.history("AAPL", rng)
    assert _demo_client["n"] == first, "cached ranges still called the broker"


def test_unknown_range_and_symbol_are_refused():
    with pytest.raises(stocks.UnknownSymbol):
        stocks.history("AAPL", "10Y")
    with pytest.raises(stocks.UnknownSymbol):
        stocks.history("not a symbol", "1D")
