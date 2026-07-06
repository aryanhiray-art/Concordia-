"""Unit tests for consensus tally + dry-run executor plumbing.

Run from backend/:  python -m pytest tests/ -q
(uses a throwaway temp DB; no network / no Robinhood calls).
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ["CONCORD_DB_PATH"] = tempfile.mkstemp(suffix=".db")[1]
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")

from app.db import db, init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import executor, voting  # noqa: E402


def _member(email: str, opted_in: int = 1) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,updated_at) VALUES(?,?,?,?,?,?)",
            (mid, opted_in, 10.0, 500.0, 10, int(time.time())),
        )
    return mid


def setup_module(_):
    init_db()
    seed_universe()


def test_tally_and_topn_selection():
    import datetime
    s = voting.open_session_for(datetime.date(2026, 6, 17))
    a, b, c = _member("a@x.com"), _member("b@x.com"), _member("c@x.com")
    voting.set_member_picks(s["id"], a, ["AAPL", "NVDA", "TSLA"])
    voting.set_member_picks(s["id"], b, ["AAPL", "NVDA", "HOOD"])
    voting.set_member_picks(s["id"], c, ["AAPL", "TSLA", "HOOD"])

    ranked = voting.tally(s["id"])
    top = {r["symbol"]: r["votes"] for r in ranked[:4]}
    assert top["AAPL"] == 3
    assert top["NVDA"] == 2 and top["TSLA"] == 2 and top["HOOD"] == 2

    closed = voting.close_and_select(s["id"])
    picks = [p["symbol"] for p in voting.session_picks(closed)]
    assert picks[0] == "AAPL"           # clear winner first
    assert len(picks) == 3              # TOP_N=3
    assert closed["status"] == "closed"


def test_replace_picks_is_idempotent():
    import datetime
    s = voting.open_session_for(datetime.date(2026, 6, 18))
    m = _member("d@x.com")
    voting.set_member_picks(s["id"], m, ["AAPL", "MSFT"])
    voting.set_member_picks(s["id"], m, ["TSLA"])      # replace
    picks = voting.member_picks(s["id"], m)
    assert [p["symbol"] for p in picks] == ["TSLA"]
    assert picks[0]["weight"] == 100.0


def test_dry_run_executor_handles_unconnected_members():
    import datetime
    s = voting.open_session_for(datetime.date(2026, 6, 19))
    m = _member("e@x.com")
    voting.set_member_picks(s["id"], m, ["AAPL", "NVDA"])
    closed = voting.close_and_select(s["id"])
    summary = executor.run(closed, force_dry_run=True)
    assert summary["mode"].startswith("DRY_RUN")
    assert summary["will_place"] is False
    # member has no Robinhood connection → reported, not crashed
    assert summary["members"][0]["note"] == "no connected agentic Robinhood account"
