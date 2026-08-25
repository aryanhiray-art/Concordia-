"""The watchdog sweep (services/watchdog.py) — per-check units + alert plumbing.

Per-check tests fabricate DB states directly (the test_renames pattern) and
call the check functions; sweep-mechanics tests swap watchdog._CHECKS for
scripted checks so alert-once / clear-on-recovery / re-alert / batching /
crash-isolation are exercised without any real condition in the way.

The sweep must be constitutionally incapable of raising: a crashing check
logs and moves on (and keeps its standing alerts), a raising mailer is
swallowed per email, and email-disabled mode prints everything to stdout.

Run from backend/:  python -m pytest tests/test_watchdog.py -q
"""
import datetime
import json
import os
import tempfile
import time
import types

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import app.calendar_us as cal  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_get, kv_set, set_armed  # noqa: E402
from app.services import adminview, mailer, voting, watchdog  # noqa: E402


def setup_module(_):
    init_db()


def teardown_module(_):
    # House pattern (see test_audit_fixes): shared temp DB — reset the mutable
    # tables + this module's kv alert keys so nothing leaks into later modules.
    with db() as conn:
        for t in ("executions", "votes", "bot_picks", "vote_sessions",
                  "voting_power_history", "login_events", "api_keys", "audit_log",
                  "rh_accounts", "member_settings", "members", "run_reports",
                  "account_snapshots", "equity_points", "member_flows"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM kv WHERE key LIKE 'wd:%'")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _member(email: str, opted_in: int = 1, alloc_type: str = "percent",
            alloc_value: float = 50.0, connect: bool = True,
            rh_status: str = "connected", role: str = "member") -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], role, now))
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,max_orders,alloc_type,"
            "alloc_value,updated_at) VALUES(?,?,10,?,?,?)",
            (mid, opted_in, alloc_type, alloc_value, now))
        if connect:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,"
                "enc_tokens,connected_at,last_refresh_at,status) "
                "VALUES(?,?,1,?,?,?,?)", (mid, f"ACC{mid}", b"enc", now, now, rh_status))
    return mid


def _bot(email: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) "
            "VALUES(?,?,'member',1,?)", (email, email.split("@")[0], int(time.time())))
    return cur.lastrowid


def _snapshot(mid: int, total_value: float) -> None:
    payload = {"allocation": {"balance": {"total_value": total_value}}}
    with db() as conn:
        conn.execute(
            "INSERT INTO account_snapshots(member_id,payload,holdings,refreshed_at,ok) "
            "VALUES(?,?,?,?,1) ON CONFLICT(member_id) DO UPDATE SET payload=excluded.payload",
            (mid, json.dumps(payload), "[]", int(time.time())))


def _equity_point(mid: int, ts: int, v: float) -> None:
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO equity_points(member_id,ts,total_value) "
                     "VALUES(?,?,?)", (mid, ts, v))


def _session_row(trade_date: str, status: str, closes_at: int | None = None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,3)", (trade_date, status, int(time.time()), closes_at))
    return cur.lastrowid


def _run_report(trade_date: str, verdict: str | None, started_at: int) -> None:
    with db() as conn:
        conn.execute(
            'INSERT INTO run_reports(trade_date,phase,mode,"trigger",will_place,'
            "started_at,finished_at,verify_verdict,summary_json) "
            "VALUES(?,?,?,?,0,?,?,?,?)",
            (trade_date, "both", "TEST", "schedule", started_at, started_at, verdict, "{}"))


def _pin(monkeypatch, y, mo, d, h, mi) -> datetime.datetime:
    fake = datetime.datetime(y, mo, d, h, mi, tzinfo=cal.ET)
    monkeypatch.setattr(watchdog, "now_et", lambda: fake)
    return fake


def _checks(monkeypatch, lst) -> None:
    monkeypatch.setattr(watchdog, "_CHECKS", lst)


def _flows(mid: int) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM member_flows WHERE member_id=? ORDER BY id", (mid,)).fetchall()]


# ─── W1: member token dead ───────────────────────────────────────────────────

def test_w1_member_token_dead_alerts_and_clears():
    mid = _member("wd-token@wd.test", rh_status="error")
    finds = watchdog._check_member_token_dead()
    hit = next(f for f in finds if f["key"] == f"member-token-dead:{mid}")
    assert hit["severity"] == "crit"
    assert "Robinhood needs reconnect" in hit["message"]
    assert "will not trade next run" in hit["message"]
    assert hit["notify"]["email"] == "wd-token@wd.test"
    assert "reconnect" in hit["notify"]["body"].lower()
    with db() as conn:
        conn.execute("UPDATE rh_accounts SET status='connected' WHERE member_id=?", (mid,))
    assert not any(f["key"] == f"member-token-dead:{mid}"
                   for f in watchdog._check_member_token_dead())


def test_w1_ignores_opted_out_and_bots():
    _member("wd-token-out@wd.test", opted_in=0, rh_status="error")
    bot = _bot("wd-token-bot@wd.test")
    with db() as conn:
        conn.execute("INSERT INTO member_settings(member_id,opted_in,updated_at) "
                     "VALUES(?,1,0)", (bot,))
        conn.execute("INSERT INTO rh_accounts(member_id,status) VALUES(?, 'error')", (bot,))
    keys = {f["key"] for f in watchdog._check_member_token_dead()}
    assert not any(k.endswith("wd-token-out") for k in keys)
    assert f"member-token-dead:{bot}" not in keys


# ─── W2: no session on the eve of a vote day ─────────────────────────────────

def test_w2_no_session_on_eve(monkeypatch):
    monkeypatch.setattr(watchdog, "cycle_phase", lambda d: "both")
    monkeypatch.setattr(voting, "latest_session", lambda status: None)
    _pin(monkeypatch, 2099, 11, 29, 18, 45)
    finds = watchdog._check_no_session_on_eve()
    assert finds and finds[0]["key"] == "no-session-on-eve"
    assert finds[0]["severity"] == "crit" and "open-vote failed" in finds[0]["message"]
    # before 18:30 → quiet (the 18:30 sweep is the judge)
    _pin(monkeypatch, 2099, 11, 29, 18, 15)
    assert watchdog._check_no_session_on_eve() == []
    # a live open ballot → quiet
    _pin(monkeypatch, 2099, 11, 29, 18, 45)
    monkeypatch.setattr(voting, "latest_session",
                        lambda status: {"id": 1, "status": "open",
                                        "closes_at": time.time() + 7200})
    assert watchdog._check_no_session_on_eve() == []


def test_w2_buy_day_eve_needs_no_ballot(monkeypatch):
    """The eve of a top-up (buy) day must not demand an open ballot — the buy
    leg executes the already-closed session (Monday evening false-alarm)."""
    monkeypatch.setattr(watchdog, "cycle_phase", lambda d: "buy")
    monkeypatch.setattr(voting, "latest_session", lambda status: None)
    _pin(monkeypatch, 2099, 11, 29, 18, 45)
    assert watchdog._check_no_session_on_eve() == []


# ─── W3: quorum risk ─────────────────────────────────────────────────────────

def test_w3_quorum_risk(monkeypatch):
    now = _pin(monkeypatch, 2099, 11, 30, 12, 0)
    sid = _session_row("2099-12-01", "open", closes_at=int(now.timestamp()) + 6 * 3600)
    sess = {"id": sid, "trade_date": "2099-12-01", "status": "open",
            "closes_at": int(now.timestamp()) + 6 * 3600}
    monkeypatch.setattr(voting, "latest_session",
                        lambda status: sess if status == "open" else None)
    finds = watchdog._check_quorum_risk()
    assert finds and finds[0]["key"] == f"quorum-risk:{sid}"
    assert finds[0]["severity"] == "warn" and "0" in finds[0]["message"]
    # A human vote clears the HUMAN half — but one voter is still below the
    # total floor, and that remaining warning is exactly the 2026-08-24 signal:
    # the bots were dead and the club was 18h from trading on a single voice.
    mid = _member("wd-quorum@wd.test")
    with db() as conn:
        conn.execute("INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
                     "VALUES(?,?,?,100,?)", (sid, mid, "AAPL", int(time.time())))
    finds = watchdog._check_quorum_risk()
    assert [f["key"] for f in finds] == [f"thin-quorum:{sid}"]
    assert f"below the {settings.min_total_voters}" in finds[0]["message"]
    # Enough voters clears it entirely.
    with db() as conn:
        for i in range(settings.min_total_voters - 1):
            other = _member(f"wd-quorum-{i}@wd.test")
            conn.execute("INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
                         "VALUES(?,?,?,100,?)", (sid, other, "AAPL", int(time.time())))
    assert watchdog._check_quorum_risk() == []
    # a ballot closing far out is not yet a risk
    sess["closes_at"] = int(now.timestamp()) + 30 * 3600
    with db() as conn:
        conn.execute("DELETE FROM votes WHERE session_id=?", (sid,))
    assert watchdog._check_quorum_risk() == []


# ─── W4: bots idle ───────────────────────────────────────────────────────────

def test_w4_bots_idle_probes_only_when_silent(monkeypatch):
    _bot("wd-bot-a@wd.test")
    _bot("wd-bot-b@wd.test")
    now = _pin(monkeypatch, 2099, 12, 1, 12, 0)
    sid = _session_row("2099-12-02", "open", closes_at=int(now.timestamp()) + 6 * 3600)
    sess = {"id": sid, "trade_date": "2099-12-02", "status": "open",
            "closes_at": int(now.timestamp()) + 6 * 3600}
    monkeypatch.setattr(voting, "latest_session",
                        lambda status: sess if status == "open" else None)
    import app.bots.ollama as ollama
    monkeypatch.setattr(ollama, "probe",
                        lambda *a, **k: {"reachable": False, "detail": "tunnel down"})
    finds = watchdog._check_bots_idle()
    assert finds and finds[0]["key"] == f"bots-idle:{sid}"
    assert finds[0]["severity"] == "warn"
    assert "unreachable" in finds[0]["message"] and "tunnel down" in finds[0]["message"]
    # endpoint reachable → the bot-vote timer may just not have fired yet
    monkeypatch.setattr(ollama, "probe", lambda *a, **k: {"reachable": True})
    assert watchdog._check_bots_idle() == []
    # a bot has voted → no probe at all (the network call is the LAST resort)
    def _no_probe(*a, **k):
        raise AssertionError("probe must not run when bots have voted")
    monkeypatch.setattr(ollama, "probe", _no_probe)
    with db() as conn:
        bot = conn.execute("SELECT id FROM members WHERE email='wd-bot-a@wd.test'").fetchone()
        conn.execute("INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
                     "VALUES(?,?,?,100,?)", (sid, bot["id"], "AAPL", int(time.time())))
    assert watchdog._check_bots_idle() == []


# ─── W5: halted/disarmed on a cycle morning ──────────────────────────────────

def test_w5_halted_or_disarmed_on_cycle_morning(monkeypatch):
    monkeypatch.setattr(watchdog, "cycle_phase", lambda d: "both")
    _pin(monkeypatch, 2099, 12, 2, 8, 0)
    object.__setattr__(settings, "execution_mode", "live")
    set_armed(False)
    kv_set("halt", "1")
    try:
        finds = watchdog._check_halted_or_disarmed()
        assert finds and finds[0]["key"] == "halted-or-disarmed-on-cycle-morning"
        assert "halted" in finds[0]["message"] and "place nothing" in finds[0]["message"]
        kv_set("halt", "0")
        finds = watchdog._check_halted_or_disarmed()
        assert finds and "disarmed" in finds[0]["message"]
        set_armed(True)
        assert watchdog._check_halted_or_disarmed() == []
        # afternoon (past execute time) → the morning check stands down
        set_armed(False)
        _pin(monkeypatch, 2099, 12, 2, 12, 0)
        assert watchdog._check_halted_or_disarmed() == []
        # dry_run mode → expected state, not an incident
        object.__setattr__(settings, "execution_mode", "dry_run")
        _pin(monkeypatch, 2099, 12, 2, 8, 0)
        assert watchdog._check_halted_or_disarmed() == []
    finally:
        kv_set("halt", "0")
        set_armed(False)


# ─── W6: missed run ──────────────────────────────────────────────────────────

def test_w6_missed_run(monkeypatch):
    d = cal.next_trading_day(datetime.date(2099, 6, 1))
    monkeypatch.setattr(watchdog, "cycle_phase", lambda x: "sell")
    _pin(monkeypatch, d.year, d.month, d.day, 11, 0)   # past execute_at + 45min
    finds = watchdog._check_missed_run()
    assert finds and finds[0]["key"] == f"missed-run:{d.isoformat()}"
    assert finds[0]["severity"] == "crit"
    # a run_reports row for today (even a stood-down one) clears the condition
    _run_report(d.isoformat(), None, started_at=12345)
    assert watchdog._check_missed_run() == []
    # idle days never alarm
    monkeypatch.setattr(watchdog, "cycle_phase", lambda x: None)
    assert watchdog._check_missed_run() == []


def test_w6_quiet_inside_the_grace_window(monkeypatch):
    d = cal.next_trading_day(datetime.date(2099, 7, 1))
    monkeypatch.setattr(watchdog, "cycle_phase", lambda x: "sell")
    _pin(monkeypatch, d.year, d.month, d.day, 9, 45)   # execute 09:31 + 45m grace
    assert watchdog._check_missed_run() == []


# ─── W7: verify failed ───────────────────────────────────────────────────────

def test_w7_verify_failed(monkeypatch):
    now = _pin(monkeypatch, 2099, 8, 2, 12, 0)
    ts = int(now.timestamp())
    _run_report("2099-08-01", "WARN", started_at=ts - 3600)
    finds = watchdog._check_verify_failed()
    assert finds and finds[0]["key"] == "verify-failed"
    assert "WARN" in finds[0]["message"] and "2099-08-01" in finds[0]["message"]
    # a newer clean report clears it
    _run_report("2099-08-02", None, started_at=ts - 60)
    assert watchdog._check_verify_failed() == []
    # an OLD failing report (not today/yesterday) never alarms
    _run_report("2099-08-02", "FAIL", started_at=ts - 3 * 86400)
    assert watchdog._check_verify_failed() == []


# ─── W8: stuck session ───────────────────────────────────────────────────────

def test_w8_stuck_session(monkeypatch):
    _pin(monkeypatch, 2026, 9, 6, 12, 0)   # Sunday
    stuck = _session_row("2026-08-25", "closed")
    fresh = _session_row("2026-09-04", "closed")    # the previous trading day
    done = _session_row("2026-08-20", "executed")
    keys = {f["key"] for f in watchdog._check_stuck_session()}
    assert f"stuck-session:{stuck}" in keys
    assert f"stuck-session:{fresh}" not in keys, "a live cycle's closed session is normal"
    assert f"stuck-session:{done}" not in keys, "executed sessions are finished business"


# ─── W9: suspected flows + member_flows dedupe ───────────────────────────────

def test_w9_suspected_flow_records_once_per_step():
    mid = _member("wd-flow@wd.test")
    _equity_point(mid, 1_000_000, 100.0)
    _equity_point(mid, 1_000_600, 150.0)
    finds = watchdog._check_suspected_flow()
    hit = next(f for f in finds if f["key"] == f"suspected-flow:{mid}:1000600")
    assert hit["severity"] == "warn"
    assert "$50.00" in hit["message"]
    assert "returns are computed around flows" in hit["message"]
    rows = _flows(mid)
    assert len(rows) == 1
    assert rows[0]["ts"] == 1_000_600 and rows[0]["amount"] == 50.0
    assert rows[0]["kind"] == "suspected" and rows[0]["source"] == "auto"
    # a second sweep re-detects the condition but records NO second row
    watchdog._check_suspected_flow()
    assert len(_flows(mid)) == 1


def test_w9_trading_in_the_window_explains_the_step():
    mid = _member("wd-flow-traded@wd.test")
    _equity_point(mid, 2_000_000, 100.0)
    _equity_point(mid, 2_000_600, 150.0)
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,ref_id,"
            "status,created_at) VALUES(0,?,?,?,?,?,?,?)",
            (mid, "AAPL", "buy", "market", f"wdflow:{mid}", "placed", 2_000_300))
    assert not any(f["key"].startswith(f"suspected-flow:{mid}:")
                   for f in watchdog._check_suspected_flow())
    assert _flows(mid) == []


def test_w9_small_steps_are_market_noise():
    mid = _member("wd-flow-noise@wd.test")
    _equity_point(mid, 3_000_000, 100.0)
    _equity_point(mid, 3_000_600, 101.5)   # 1.5% < the 2% step floor
    assert not any(f["key"].startswith(f"suspected-flow:{mid}:")
                   for f in watchdog._check_suspected_flow())
    assert _flows(mid) == []


# ─── W10: disk ───────────────────────────────────────────────────────────────

def test_w10_disk(monkeypatch):
    monkeypatch.setattr(watchdog.os, "statvfs",
                        lambda p: types.SimpleNamespace(f_blocks=100, f_bavail=5,
                                                        f_frsize=4096))
    finds = watchdog._check_disk()
    assert finds and finds[0]["key"] == "disk" and finds[0]["severity"] == "crit"
    monkeypatch.setattr(watchdog.os, "statvfs",
                        lambda p: types.SimpleNamespace(f_blocks=100, f_bavail=50,
                                                        f_frsize=4096))
    assert watchdog._check_disk() == []


# ─── W11: backup stale ───────────────────────────────────────────────────────

def test_w11_backup_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "_backup_dir", lambda: tmp_path)
    finds = watchdog._check_backup_stale()
    assert finds and finds[0]["key"] == "backup-stale"
    assert "no backups found" in finds[0]["message"]
    f = tmp_path / "concord-20990101-000000.db"
    f.write_bytes(b"x")
    assert watchdog._check_backup_stale() == []
    old = time.time() - 60 * 3600
    os.utime(f, (old, old))
    finds = watchdog._check_backup_stale()
    assert finds and "concord-backup.timer" in finds[0]["message"]


def test_backup_dir_follows_the_backup_jobs_own_rules(monkeypatch, tmp_path):
    """_backup_dir reads jobs/backup.py's _paths() — so the env override the
    job honours is honoured here too, never a hardcoded guess."""
    monkeypatch.setenv("CONCORD_BACKUP_DIR", str(tmp_path))
    assert watchdog._backup_dir() == tmp_path


# ─── W12: underfunded stake ──────────────────────────────────────────────────

def test_w12_underfunded_stake_alerts_and_clears_when_funded():
    mid = _member("wd-fund@wd.test", alloc_type="dollar", alloc_value=500.0)
    _snapshot(mid, total_value=120.0)
    finds = watchdog._check_underfunded_stake()
    hit = next(f for f in finds if f["key"] == f"underfunded-stake:{mid}")
    assert hit["severity"] == "warn"
    assert "can only deploy what's there" in hit["message"]
    assert hit["notify"]["email"] == "wd-fund@wd.test"
    assert "Add funds or lower your stake" in hit["notify"]["body"]
    _snapshot(mid, total_value=600.0)   # funded → clears
    assert not any(f["key"] == f"underfunded-stake:{mid}"
                   for f in watchdog._check_underfunded_stake())


def test_w12_percent_stakes_cannot_be_underfunded():
    mid = _member("wd-fund-pct@wd.test", alloc_type="percent", alloc_value=50.0)
    _snapshot(mid, total_value=100.0)
    assert not any(f["key"] == f"underfunded-stake:{mid}"
                   for f in watchdog._check_underfunded_stake())


# ─── sweep mechanics: alert-once, clear, re-alert, batching, crash isolation ─

def test_sweep_alerts_once_clears_and_realerts(monkeypatch, capsys):
    state = {"on": True}

    def chk():
        return ([{"key": "tst-a:1", "severity": "warn", "message": "cond A broke"}]
                if state["on"] else [])
    _checks(monkeypatch, [("tst-a", chk)])

    res = watchdog.sweep()
    assert res["new"] == ["tst-a:1"]
    out = capsys.readouterr().out
    assert "Concordia watchdog: 1 new alert(s)" in out and "cond A broke" in out
    payload = json.loads(kv_get("wd:tst-a:1"))
    assert payload["severity"] == "warn"
    assert payload["message"] == "cond A broke" and payload["since"]
    with db() as conn:
        row = conn.execute("SELECT detail_json FROM audit_log "
                           "WHERE action='watchdog_alert' AND target='tst-a:1'").fetchone()
    assert row and json.loads(row["detail_json"])["severity"] == "warn"

    # second sweep: condition persists → no re-announcement
    res = watchdog.sweep()
    assert res["new"] == []
    assert "Concordia watchdog" not in capsys.readouterr().out

    # condition clears → kv emptied
    state["on"] = False
    watchdog.sweep()
    assert not kv_get("wd:tst-a:1")

    # recurrence re-alerts
    state["on"] = True
    res = watchdog.sweep()
    assert res["new"] == ["tst-a:1"]
    assert "Concordia watchdog: 1 new alert(s)" in capsys.readouterr().out


def test_sweep_survives_a_crashing_check_and_keeps_its_alerts(monkeypatch, capsys):
    _checks(monkeypatch, [("tst-b", lambda: [{"key": "tst-b:x", "severity": "crit",
                                              "message": "B down"}])])
    watchdog.sweep()
    assert kv_get("wd:tst-b:x")
    capsys.readouterr()

    def bomb():
        raise RuntimeError("boom")
    _checks(monkeypatch, [("tst-b", bomb),
                          ("tst-c", lambda: [{"key": "tst-c:1", "severity": "warn",
                                              "message": "C too"}])])
    res = watchdog.sweep()          # must not raise
    assert res["crashed"] == ["tst-b"]
    assert kv_get("wd:tst-b:x"), "a crashed check must not clear its standing alerts"
    assert kv_get("wd:tst-c:1"), "the other checks still ran"
    assert "crashed" in capsys.readouterr().out

    # observed recovery → both cleared
    _checks(monkeypatch, [("tst-b", lambda: []), ("tst-c", lambda: [])])
    watchdog.sweep()
    assert not kv_get("wd:tst-b:x") and not kv_get("wd:tst-c:1")


def test_batched_email_and_member_notification(monkeypatch):
    _member("wd-owner@wd.test", role="owner", connect=False)
    object.__setattr__(settings, "resend_api_key", "test-key")   # conftest restores
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, html, text: sent.append((to, subject, text)) or True)
    _checks(monkeypatch, [("tst-d", lambda: [
        {"key": "tst-d:1", "severity": "crit", "message": "D down",
         "notify": {"email": "wd-affected@wd.test", "subject": "Concordia: act",
                    "body": "please act"}}])])
    watchdog.sweep()
    batch = [s for s in sent if s[1] == "Concordia watchdog: 1 new alert(s)"]
    assert any(to == "wd-owner@wd.test" for to, _s, _t in batch), \
        "admins get ONE batched email per sweep"
    to_counts: dict = {}
    for to, _s, _t in batch:
        to_counts[to] = to_counts.get(to, 0) + 1
    assert all(n == 1 for n in to_counts.values()), \
        "exactly ONE batched email per recipient per sweep — never one per alert"
    assert all("D down" in t for _to, _s, t in batch)
    assert any(to == "wd-affected@wd.test" and s == "Concordia: act"
               for to, s, _t in sent), "the affected member is mailed directly"
    # second sweep: nothing new → nothing sent
    sent.clear()
    watchdog.sweep()
    assert sent == []


def test_raising_mailer_never_breaks_the_sweep(monkeypatch):
    object.__setattr__(settings, "resend_api_key", "test-key")

    def _boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(mailer, "send", _boom)
    _checks(monkeypatch, [("tst-e", lambda: [
        {"key": "tst-e:1", "severity": "warn", "message": "E",
         "notify": {"email": "wd-e@wd.test", "subject": "s", "body": "b"}}])])
    res = watchdog.sweep()          # must not raise
    assert res["new"] == ["tst-e:1"]
    assert kv_get("wd:tst-e:1"), "the alert is still recorded when mail fails"


def test_email_disabled_prints_member_notification(monkeypatch, capsys):
    _checks(monkeypatch, [("tst-f", lambda: [
        {"key": "tst-f:1", "severity": "warn", "message": "F",
         "notify": {"email": "wd-f@wd.test", "subject": "Concordia: fund",
                    "body": "add funds"}}])])
    watchdog.sweep()
    out = capsys.readouterr().out
    assert "email disabled" in out
    assert "To: wd-f@wd.test" in out and "add funds" in out


# ─── G4: the console surfaces active alerts ──────────────────────────────────

def test_panel_alerts_surface_active_findings_crits_first(monkeypatch):
    _checks(monkeypatch, [("tst-p", lambda: [
        {"key": "tst-p:warned", "severity": "warn", "message": "p warn"},
        {"key": "tst-p:critical", "severity": "crit", "message": "p crit"}])])
    watchdog.sweep()
    alerts = adminview.watchdog_alerts()
    mine = [a for a in alerts if a["key"].startswith("tst-p:")]
    assert [a["key"] for a in mine] == ["tst-p:critical", "tst-p:warned"]
    assert mine[0]["severity"] == "crit" and mine[0]["message"] == "p crit"
    assert mine[0]["since"]
    p = adminview.panel()
    assert any(a["key"] == "tst-p:critical" for a in p["alerts"])
    # cleared conditions disappear from the console
    _checks(monkeypatch, [("tst-p", lambda: [])])
    watchdog.sweep()
    assert not any(a["key"].startswith("tst-p:") for a in adminview.watchdog_alerts())
