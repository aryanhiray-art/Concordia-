"""ledger_trueup: phantom club-ledger rows are zeroed, real ones never are.

A phantom = the derived ledger says the member holds a symbol while the live
account holds none of it. They come from old sells whose estimated quantities
the broker feed can no longer true up, place no trades, and flag the member's
verification WARN every cycle (member 2 carried 19).
"""
import os
import tempfile
import time

# Point the app at a throwaway DB BEFORE importing app modules. Without this the
# module falls through to the working-directory concord.db, where its fixed
# tu1/tu2/tu3 emails survive the run and the NEXT standalone run dies on the
# members.email UNIQUE constraint. (In the full suite the setdefault is a no-op —
# an earlier module already claimed the shared temp DB.)
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.services import reconcile  # noqa: E402


def setup_module(_):
    init_db()


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
    # The feed corroborates both phantoms: the member sold them by hand (a
    # 'user' sell, which the derived view ignores by design) after the club
    # bought them. A name whose latest order is a BUY did not leave the
    # account and is never trued up (see test_uncorroborated…).
    monkeypatch.setattr(positions, "_fetch_full_order_feed",
                        lambda client, acct, **kw: _feed(
                            ("AES", "buy", 20), ("AES", "sell", 10, "user"),
                            ("AWK", "buy", 20), ("AWK", "sell", 10, "user"),
                            ("KO", "buy", 20)))


def _feed(*events):
    """(symbol, side, days_ago[, agent]) → filled feed rows, real-shaped."""
    import datetime
    out = []
    for e in events:
        sym, side, days = e[0], e[1], e[2]
        agent = e[3] if len(e) > 3 else "agentic"
        ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        out.append({"id": f"{sym}-{side}-{days}", "symbol": sym, "side": side, "state": "filled",
                    "quantity": "0.100000", "cumulative_quantity": "0.100000",
                    "placed_agent": agent, "created_at": ts.isoformat().replace("+00:00", "Z")})
    return out


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


def test_uncorroborated_disappearance_is_left_alone(monkeypatch):
    """Derived holds AES and the account shows none of it — but the feed's
    latest AES order is a BUY. Shares do not leave an account without a sell:
    this is a ticker the broker now reports under another symbol, or a
    positions read that missed it. Zeroing it would hide real shares from every
    sell leg for good. Left alone, on the record."""
    from app.services import positions
    from app.robinhood import vault
    mid = _member("tu-uncorr@t.test")
    monkeypatch.setattr(vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"AES": 0.3, "KO": 0.1})
    monkeypatch.setattr(positions, "live_positions_strict",
                        lambda m: [{"symbol": "KO", "quantity": 0.1}])
    monkeypatch.setattr(positions, "_fetch_full_order_feed",
                        lambda client, acct, **kw: _feed(("AES", "buy", 20), ("KO", "buy", 20)))
    res = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert res["zeroed"] == [] and res["uncorroborated"] == ["AES"]
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions WHERE member_id=?",
                            (mid,)).fetchone()[0] == 0
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='trueup_uncorroborated' "
                         "AND target=?", (str(mid),)).fetchone()[0]
    assert n == 1


def test_an_empty_account_read_is_refused_not_zeroed(monkeypatch):
    """The whole club position 'missing' from a successful positions read is a
    read to distrust, not a member who sold everything: refuse, audit, write
    nothing — an admin who knows better passes force=True."""
    from app.services import positions
    from app.robinhood import vault
    mid = _member("tu-empty@t.test")
    monkeypatch.setattr(vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {"AES": 0.3, "AWK": 0.2})
    monkeypatch.setattr(positions, "live_positions_strict", lambda m: [])
    monkeypatch.setattr(positions, "_fetch_full_order_feed",
                        lambda client, acct, **kw: _feed(("AES", "buy", 20), ("AES", "sell", 10, "user"),
                                                          ("AWK", "buy", 20), ("AWK", "sell", 10, "user")))
    res = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert res["ok"] is False and "empty" in res["refused"] and res["zeroed"] == []
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions WHERE member_id=?",
                            (mid,)).fetchone()[0] == 0
    forced = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False, force=True)
    assert {z["symbol"] for z in forced["zeroed"]} == {"AES", "AWK"}


def test_mass_zeroing_is_refused(monkeypatch):
    """Six club names, five read as phantoms in one sweep: that is not
    estimate drift, it is a bad read. Refused unless forced."""
    from app.services import positions
    from app.robinhood import vault
    mid = _member("tu-mass@t.test")
    syms = ["A", "B", "C", "D", "E", "F"]
    monkeypatch.setattr(vault, "ensure_client", lambda m: object())
    monkeypatch.setattr(positions, "club_shares_derived",
                        lambda m, client=None, account_number=None: {s: 0.1 for s in syms})
    monkeypatch.setattr(positions, "live_positions_strict",
                        lambda m: [{"symbol": "F", "quantity": 0.1}])
    monkeypatch.setattr(positions, "_fetch_full_order_feed",
                        lambda client, acct, **kw: _feed(*[(s, "buy", 20) for s in syms],
                                                          *[(s, "sell", 10, "user") for s in syms[:5]]))
    res = reconcile.ledger_trueup(mid, account_number=f"ACC{mid}", dry_run=False)
    assert res["ok"] is False and "5 of 6" in res["refused"]
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='trueup_refused' "
                         "AND target=?", (str(mid),)).fetchone()[0]
    assert n == 1
