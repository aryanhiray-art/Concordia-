"""Bot voting runs as retryable passes — the 2026-08-24 regression.

That morning the single Monday-morning bot run hit the systemd start timeout and
was killed with ZERO bots voted, so the club executed real money on a tally with
one voter. The fix is three passes a week (Wed/Sat/Mon), each attempted several
times, with two properties these tests pin down:

  * RESUMABLE — a retry re-runs only the bots the killed attempt never reached.
  * DURABLE   — a pass that fails outright leaves the previous pass's votes
                standing, so the basket is never emptied by a dead job.
"""
import datetime
import os
import tempfile
import time

os.environ["CONCORD_DB_PATH"] = tempfile.mkstemp(suffix=".db")[1]
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# House convention: every DB-touching module declares TOP_N so whichever one
# pytest imports first freezes `settings` the same way (see conftest.py).
os.environ.setdefault("TOP_N", "3")

from app.db import db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import bots, voting  # noqa: E402

_PICKS = ["AAPL", "MSFT", "NVDA"]


def setup_module(_):
    init_db()
    seed_universe()
    bots.seed_bots()


def _decider(fail_after: int | None = None, symbol: str = "AAPL"):
    """Stub ollama.decide: succeed, optionally raising once `fail_after` calls land."""
    calls: list[int] = []

    def decide(strategy, tickers, prices):
        calls.append(1)
        if fail_after is not None and len(calls) > fail_after:
            raise RuntimeError("Read timed out. (read timeout=120)")
        return {"picks": [{"ticker": symbol, "pct": 100.0}], "thesis": "t"}

    decide.calls = calls  # type: ignore[attr-defined]
    return decide


def _votes_for(sid: int) -> dict[int, tuple[str, int]]:
    with db() as conn:
        return {
            r["member_id"]: (r["symbol"], r["created_at"])
            for r in conn.execute(
                "SELECT v.member_id, v.symbol, v.created_at FROM votes v "
                "JOIN members m ON m.id=v.member_id "
                "WHERE v.session_id=? AND COALESCE(m.is_bot,0)=1", (sid,)
            ).fetchall()
        }


def test_retry_resumes_and_skips_bots_that_already_voted(monkeypatch):
    s = voting.open_session_for(datetime.date(2029, 6, 4))
    total = len(bots.bot_members())

    # Attempt 1 dies partway, exactly like a systemd timeout kill.
    first = _decider(fail_after=5)
    monkeypatch.setattr(bots.ollama, "decide", first)
    r1 = bots.cast_votes(s, skip_voted_since=1)
    assert r1["voted"] == 5
    assert r1["failed"] == total - 5

    # Attempt 2 (same pass day) must only touch the bots attempt 1 never reached.
    second = _decider()
    monkeypatch.setattr(bots.ollama, "decide", second)
    r2 = bots.cast_votes(s, skip_voted_since=1)
    assert r2["already"] == 5, "bots that already voted this pass must be skipped"
    assert len(second.calls) == total - 5, "a retry must not re-run completed bots"
    assert r2["voted"] == total - 5
    assert len(_votes_for(s["id"])) == total, "two cheap attempts reach full turnout"


def test_failed_pass_keeps_the_previous_passs_votes(monkeypatch):
    """The core guarantee: if Monday dies, Saturday's basket still stands."""
    s = voting.open_session_for(datetime.date(2029, 6, 5))

    monkeypatch.setattr(bots.ollama, "decide", _decider(symbol="AAPL"))
    bots.cast_votes(s, skip_voted_since=1)
    before = _votes_for(s["id"])
    assert before and all(v[0] == "AAPL" for v in before.values())

    # Next pass: every single inference fails (Ollama unreachable).
    dead = _decider(fail_after=0)
    monkeypatch.setattr(bots.ollama, "decide", dead)
    r = bots.cast_votes(s, skip_voted_since=int(time.time()) + 60)
    assert r["voted"] == 0 and r["failed"] == len(before)

    assert _votes_for(s["id"]) == before, "a dead pass must not erase prior votes"


def test_new_pass_redecides_on_fresh_prices(monkeypatch):
    """A later pass day rolls the cutoff, so every bot re-decides."""
    s = voting.open_session_for(datetime.date(2029, 6, 6))

    monkeypatch.setattr(bots.ollama, "decide", _decider(symbol="AAPL"))
    bots.cast_votes(s, skip_voted_since=1)
    assert all(v[0] == "AAPL" for v in _votes_for(s["id"]).values())

    # Cutoff in the future = "nothing counts as done yet" = a fresh pass.
    fresh = _decider(symbol="MSFT")
    monkeypatch.setattr(bots.ollama, "decide", fresh)
    r = bots.cast_votes(s, skip_voted_since=int(time.time()) + 60)
    assert r["already"] == 0
    assert len(fresh.calls) == len(bots.bot_members())
    assert all(v[0] == "MSFT" for v in _votes_for(s["id"]).values())


def test_no_cutoff_redecides_everyone(monkeypatch):
    """`--force` (skip_voted_since=None) re-runs the whole roster."""
    s = voting.open_session_for(datetime.date(2029, 6, 7))
    monkeypatch.setattr(bots.ollama, "decide", _decider())
    bots.cast_votes(s, skip_voted_since=1)

    again = _decider()
    monkeypatch.setattr(bots.ollama, "decide", again)
    r = bots.cast_votes(s, skip_voted_since=None)
    assert r["already"] == 0
    assert len(again.calls) == len(bots.bot_members())
