"""ledger_trueup: phantom club-ledger rows are zeroed, real ones never are.

A phantom = the derived ledger says the member holds a symbol while the live
account holds none of it. They come from old sells whose estimated quantities
the broker feed can no longer true up, place no trades, and flag the member's
verification WARN every cycle (member 2 carried 19).
"""
import time

import pytest

from app.db import db
from app.services import reconcile


def _member(email: str) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?, 'member', ?)",
            (email, email.split("@")[0], now))
        mid = cur.lastrowid
        conn.execute("INSERT INTO member_settings(member_id,opted_in,updated_at) VALUES(?,1,?)",
                     (mid, now))
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,status) VALUES(?,?,1,'x',?, 'connected')",
            (mid, f"ACC{mid}", now))
    return mid


@pytest.fixture
def wired(monkeypatch):
    """Derived says AES+AWK+KO are held; the live account holds only KO."""
    from app.services import positions
    from app.robinhood import vault
    monkeypatch.setattr(vault, "ensure_client", lambda mid: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda mid, client=None, account_number=None:
                        {"AES": 0.342573, "AWK": 7e-06, "KO": 0.061})
    monkeypatch.setattr(positions, "live_positions_strict",
                        lambda mid: [{"symbol": "KO", "quantity": 0.061}])


def test_phantoms_zeroed_held_names_untouched(wired):
    mid = _member("tu1@t.test")
    prev = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=True)
    assert {z["symbol"] for z in prev["zeroed"]} == {"AES", "AWK"}, \
        "KO is genuinely held and must never be trued up"
    with db() as conn:   # dry run wrote nothing
        assert conn.execute("SELECT COUNT(*) FROM executions WHERE member_id=?",
                            (mid,)).fetchone()[0] == 0

    real = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert len(real["zeroed"]) == 2
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, side, dollar_amount, ref_id FROM executions WHERE member_id=?",
            (mid,)).fetchall()
    assert {(r["symbol"], r["side"], r["dollar_amount"]) for r in rows} == \
        {("AES", "sell", 0.0), ("AWK", "sell", 0.0)}, "append-only $0 rows, renames-style"
    assert all(r["ref_id"].startswith("TRUEUP:") for r in rows)

    again = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert again["zeroed"] == [], "same-day rerun is idempotent (ref_id dedup)"


def test_recent_execution_defers_the_trueup(wired):
    """An exit placed within 48h may simply not have reached the feed yet —
    zeroing it would double-count the sell once the fill lands."""
    mid = _member("tu2@t.test")
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,created_at) VALUES(1,?,?,'sell','market',5.0,?, 'placed',?)",
            (mid, "AES", f"recent-{mid}", int(time.time())))
    prev = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=True)
    assert {z["symbol"] for z in prev["zeroed"]} == {"AWK"}, "AES is in flight — left alone"


def test_failed_live_read_raises_instead_of_zeroing_everything(monkeypatch):
    """The one catastrophic wrong answer: a throttled positions read looking
    like an empty account, which would mark the ENTIRE ledger phantom. The
    strict reader raises, and the raise must propagate to the per-member
    containment in reconcile_all — never be swallowed into an empty live map."""
    from app.services import positions
    from app.robinhood import vault
    mid = _member("tu3@t.test")
    monkeypatch.setattr(vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"AES": 0.3})
    def boom(m):
        raise RuntimeError("throttled")
    monkeypatch.setattr(positions, "live_positions_strict", boom)
    with pytest.raises(RuntimeError):
        reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
