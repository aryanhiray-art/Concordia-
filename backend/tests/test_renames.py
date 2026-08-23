"""Ticker-rename machinery (services/renames.py) — the AVB→VMRK class of bug.

A corporate action renames a held ticker; the account converts in place but the
club ledger (executions) keeps the old symbol: a phantom the executor can never
sell, while the real position looks personal. detect() must pair the phantom
with the unledgered live position; migrate() must repair the ledger, true the
share count (renames rarely convert 1:1), and swap the universe row.
"""
import json
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])

from app.db import db, init_db  # noqa: E402
from app.services import positions, renames  # noqa: E402


def setup_module(_):
    init_db()


def _member(email: str, connected: bool = True) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", now))
        mid = cur.lastrowid
        conn.execute("INSERT INTO member_settings(member_id,opted_in,updated_at) VALUES(?,1,?)",
                     (mid, now))
        if connected:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,status,connected_at) "
                "VALUES(?,?,1,'connected',?)", (mid, f"ACC{mid}", now))
    return mid


def _snapshot(mid: int, holdings: list[dict]) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO account_snapshots(member_id,payload,holdings,refreshed_at,ok) "
            "VALUES(?,?,?,?,1) ON CONFLICT(member_id) DO UPDATE SET holdings=excluded.holdings",
            (mid, "{}", json.dumps(holdings), int(time.time())))


def _session() -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,top_n) VALUES(?, 'executed', 20)",
            (f"2099-01-{_session.n:02d}",))
        _session.n += 1
        return cur.lastrowid
_session.n = 1


def _buy(sid: int, mid: int, symbol: str, qty: float, usd: float) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,place_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, mid, symbol, "buy", "market", usd, f"T:{sid}:{mid}:{symbol}", "placed",
             json.dumps({"_concord_est_qty": qty}), int(time.time())))


def test_detect_pairs_phantom_with_stray_and_migrate_repairs_everything():
    sid = _session()
    m1 = _member("rename-a@x.test")
    m2 = _member("rename-b@x.test")
    # both bought OLDCO; the account now holds NEWCO instead (2.8:1 conversion)
    _buy(sid, m1, "OLDCO", qty=0.0269, usd=5.00)
    _buy(sid, m2, "OLDCO", qty=0.0056, usd=1.04)
    _snapshot(m1, [{"symbol": "NEWCO", "quantity": 0.0752, "value": 4.90, "pnl": 0.0}])
    _snapshot(m2, [{"symbol": "NEWCO", "quantity": 0.0156, "value": 1.02, "pnl": 0.0}])
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO universe(symbol,name,sort_order,active) "
                     "VALUES('OLDCO','Old Company',1,1)")

    pairs = renames.detect()
    hit = next(p for p in pairs if p["old"] == "OLDCO")
    assert hit["new"] == "NEWCO"
    assert sorted(hit["members"]) == ["rename-a", "rename-b"]
    assert abs(hit["live_value"] - 5.92) < 0.01

    res = renames.migrate("OLDCO", "NEWCO", name="New Company")
    assert res["execution_rows"] == 2
    # ledger now tracks NEWCO at the ACCOUNT's share count, not the estimate
    assert abs(positions.club_shares(m1).get("NEWCO", 0) - 0.0752) < 1e-6
    assert abs(positions.club_shares(m2).get("NEWCO", 0) - 0.0156) < 1e-6
    assert positions.club_shares(m1).get("OLDCO", 0) == 0
    with db() as conn:
        old = conn.execute("SELECT active FROM universe WHERE symbol='OLDCO'").fetchone()
        new = conn.execute("SELECT active, name FROM universe WHERE symbol='NEWCO'").fetchone()
    assert old["active"] == 0
    assert new["active"] == 1 and new["name"] == "New Company"
    # drift is resolved — the pair no longer detects
    assert not any(p["old"] == "OLDCO" for p in renames.detect())


def test_migrate_is_idempotent():
    before = [dict(r) for r in _rows("NEWCO")]
    res = renames.migrate("OLDCO", "NEWCO")
    assert res["execution_rows"] == 0 and res["adjustments"] == []
    assert [dict(r) for r in _rows("NEWCO")] == before


def _rows(symbol: str):
    with db() as conn:
        return conn.execute(
            "SELECT id,member_id,symbol,side,ref_id FROM executions WHERE symbol=? ORDER BY id",
            (symbol,)).fetchall()


def test_ambiguous_members_do_not_pair():
    sid = _session()
    m = _member("rename-ambig@x.test")
    _buy(sid, m, "GONE1", qty=0.1, usd=5.0)
    # two unledgered positions → ambiguous → no pair may be reported
    _snapshot(m, [
        {"symbol": "MYSTERY1", "quantity": 0.2, "value": 6.0, "pnl": 0.0},
        {"symbol": "MYSTERY2", "quantity": 0.3, "value": 7.0, "pnl": 0.0},
    ])
    assert not any(p["old"] == "GONE1" for p in renames.detect())


def test_value_match_disambiguates_among_personal_positions():
    """v2: the owner's account holds personal positions alongside the renamed
    one — the phantom must pair with the stray whose value matches the club's
    net dollars in, not fail on ambiguity (v1 did, and the incident was only
    visible via a member with a clean account)."""
    sid = _session()
    m = _member("rename-owner@x.test")
    _buy(sid, m, "GONE2", qty=0.05, usd=5.00)
    _snapshot(m, [
        {"symbol": "FOUND2", "quantity": 0.075, "value": 4.90, "pnl": 0.0},   # fits 0.5–2× of $5
        {"symbol": "PERSNL", "quantity": 1.0, "value": 60.0, "pnl": 0.0},     # way outside the band
    ])
    pairs = renames.detect()
    hit = next(p for p in pairs if p["old"] == "GONE2")
    assert hit["new"] == "FOUND2"


def test_two_plausible_strays_refuse_to_pair():
    """Two strays inside the value band = genuinely ambiguous — guess nothing."""
    sid = _session()
    m = _member("rename-ambig2@x.test")
    _buy(sid, m, "GONE3", qty=0.05, usd=5.00)
    _snapshot(m, [
        {"symbol": "MAYBE1", "quantity": 0.07, "value": 4.80, "pnl": 0.0},
        {"symbol": "MAYBE2", "quantity": 0.08, "value": 5.20, "pnl": 0.0},
    ])
    assert not any(p["old"] == "GONE3" for p in renames.detect())


def test_check_and_alert_fires_once_per_pair(capsys):
    """The refresher-path watch: first sighting audits + announces (stdout when
    email is off), repeats stay quiet, and a migrated pair clears its dedup."""
    from app.db import db as _db, kv_get

    found = renames.check_and_alert()
    assert any(p["old"] == "GONE2" for p in found)
    out1 = capsys.readouterr().out
    assert "GONE2" in out1 and "FOUND2" in out1
    assert kv_get("rename_alerted:GONE2>FOUND2")
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='rename_detected' "
                           "AND target='GONE2->FOUND2'").fetchone()
    assert row is not None

    renames.check_and_alert()
    assert "GONE2" not in capsys.readouterr().out, "second sighting must not re-announce"

    renames.migrate("GONE2", "FOUND2")
    renames.check_and_alert()   # pair resolved → dedup key cleared for any future re-emergence
    assert not kv_get("rename_alerted:GONE2>FOUND2")
