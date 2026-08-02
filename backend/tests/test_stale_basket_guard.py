"""The buy leg must never buy a basket that is not this cycle's.

Found by audit on the LIVE system, while it was armed and in live mode:
executor._run_today_locked picked the buy-leg session with
voting.latest_session("closed"), which is unbounded — "most recent row whose
status is closed", with no notion of when. Session 14 (2026-07-20) never
flipped to 'executed' because three orders errored, so it stayed 'closed' and
was the ONLY such row. Any buy-phase run — a second timer fire, the admin
Execute-now button, or a buy day whose own cycle never reached 'closed' — would
have placed REAL market buys of a basket the club deliberately exited six
cycles earlier.

These tests pin the guard: a stale basket is refused (and audited) while a
genuine prior-trading-day basket still trades.

Run from backend/:  python -m pytest tests/test_stale_basket_guard.py -q
"""
import datetime as dt
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.db import init_db  # noqa: E402
from app.services import executor  # noqa: E402


def setup_module(_):
    init_db()


def _days_ago(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def test_the_exact_production_case_is_refused():
    """Session 14 sat 12 days stale while the club was armed and live."""
    age = executor._session_age_days({"trade_date": "2026-07-20"})
    assert age is not None and age > executor.STALE_BASKET_DAYS


@pytest.mark.parametrize("days", [1, 2, 3, 4, 5])
def test_a_real_cycle_still_trades(days):
    """Sell and buy are consecutive TRADING days; a long weekend plus a holiday
    must still be inside the window or the guard would block real cycles."""
    age = executor._session_age_days({"trade_date": _days_ago(days)})
    assert age is not None and age <= executor.STALE_BASKET_DAYS


@pytest.mark.parametrize("days", [6, 12, 90])
def test_anything_older_is_refused(days):
    age = executor._session_age_days({"trade_date": _days_ago(days)})
    assert age is not None and age > executor.STALE_BASKET_DAYS


def test_an_unparseable_date_does_not_block_trading():
    """The gate must fail OPEN on a formatting quirk: refusing to trade because
    a date could not be parsed would be its own outage."""
    assert executor._session_age_days({"trade_date": "garbage"}) is None
    assert executor._session_age_days({}) is None


def test_buy_phase_refuses_the_stale_session_without_placing(monkeypatch):
    """End to end through _run_today_locked: a stale 'closed' session must
    return a refusal and never reach run() — which is what places orders."""
    stale = {"id": 14, "trade_date": "2026-07-20", "status": "closed"}
    monkeypatch.setattr(executor.voting, "latest_session", lambda status: stale)
    monkeypatch.setattr(executor, "cycle_phase", lambda *_a, **_k: "buy", raising=False)

    import app.calendar_us as cal
    monkeypatch.setattr(cal, "cycle_phase", lambda *_a, **_k: "buy")

    called = {"run": False}

    def _boom(*_a, **_k):
        called["run"] = True
        raise AssertionError("run() must not be reached for a stale basket")

    monkeypatch.setattr(executor, "run", _boom)

    out = executor._run_today_locked(force_dry_run=True)
    assert called["run"] is False
    assert out["orders"] == []
    assert out.get("stale_session") == 14
    assert "stale" in out["note"].lower()
