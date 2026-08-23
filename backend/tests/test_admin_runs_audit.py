"""Tests for the admin run/audit/login browsers + manual-trigger audit + the
startup config-change watch.

Contract (pinned — the web console is built to it independently):
- GET /api/admin/runs?limit=&before=   → {"runs":[{id, trade_date, phase, mode,
  trigger, will_place(bool), started_at, finished_at, placed, skipped, errors,
  verify_verdict}]} newest first; summary_json stays OUT of the list.
- GET /api/admin/runs/{id}             → the same fields + "summary" = parsed
  summary_json; 404 on an unknown id.
- GET /api/admin/audit?limit=&before=&action=&actor= → {"entries":[{id, ts,
  actor, action, target, detail}]}; detail = parsed detail_json or null;
  newest first; filters exact-match, optional and combinable.
- GET /api/admin/logins?limit=         → {"logins":[{ts, member_id,
  display_name, email, ip, kind}]} newest first, joined to members, bots out.
- POST /api/admin/run/{phase} audits (admin_email, "manual_trigger", phase,
  {"dry_run": ...}) BEFORE executing, on every valid branch; the execute
  branch threads trigger="manual:<email>" into the run_reports row.
- app.main.check_config_changed: first boot stores the fingerprint silently; a
  flip audits ("system","config_changed",None,{"changed":{key:[old,new]}})
  with ONLY the changed keys.

All four GETs are DB-only — browsing history must never generate broker
traffic (same freshness philosophy as /panel).

Run from backend/:  python -m pytest tests/test_admin_runs_audit.py -q
(members inserted directly + per-test spoofed IPs, same idioms as
test_admin_panel.py.)
"""
import json
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module sets, or the whole suite's
# TOP_N/EXECUTION_MODE shift (same rationale as test_admin_panel.py).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_main  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import audit, db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]

RUN_LIST_FIELDS = {"id", "trade_date", "phase", "mode", "trigger", "will_place",
                   "started_at", "finished_at", "placed", "skipped", "errors",
                   "verify_verdict"}


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.82.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, role: str = "member", is_bot: int = 0) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], role, hash_password("runs-pass-11"), now, is_bot, now),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


def _insert_run(**kw) -> int:
    row = {"trade_date": "2026-08-21", "phase": "both", "mode": "DRY_RUN·both",
           "trigger": "schedule", "will_place": 0, "started_at": 1000,
           "finished_at": 1005, "placed": 0, "skipped": 0, "errors": 0,
           "verify_verdict": None, "summary_json": json.dumps({"note": "seed"})}
    row.update(kw)
    with db() as conn:
        cur = conn.execute(
            'INSERT INTO run_reports(trade_date,phase,mode,"trigger",will_place,'
            "started_at,finished_at,placed,skipped,errors,verify_verdict,summary_json) "
            "VALUES(:trade_date,:phase,:mode,:trigger,:will_place,:started_at,"
            ":finished_at,:placed,:skipped,:errors,:verify_verdict,:summary_json)",
            row,
        )
        return cur.lastrowid


def _admin_tok() -> tuple[str, str]:
    """A fresh admin per call — unique email so audit-actor assertions can't
    collide across tests in the shared process DB."""
    email = f"runs-admin-{_ip_counter[0]}-{int(time.time()*1000) % 100000}@aclub.test"
    _, tok = _mk_member(email, role="admin")
    return email, tok


# ─── /runs list ──────────────────────────────────────────────────────────────

def test_runs_list_shape_newest_first_no_summary():
    _, tok = _admin_tok()
    a = _insert_run(trade_date="2026-08-18", trigger="schedule")
    b = _insert_run(trade_date="2026-08-19", trigger="manual:owner@x.com",
                    will_place=1, placed=7, skipped=2, errors=1,
                    verify_verdict="PASS")
    r = client.get("/api/admin/runs?limit=2", headers=_hdrs(tok))
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert [x["id"] for x in runs] == [b, a]               # newest first
    for x in runs:
        assert set(x) == RUN_LIST_FIELDS                   # summary_json stays out
    top = runs[0]
    assert top["trigger"] == "manual:owner@x.com"
    assert top["will_place"] is True and runs[1]["will_place"] is False
    assert (top["placed"], top["skipped"], top["errors"]) == (7, 2, 1)
    assert top["verify_verdict"] == "PASS"


def test_runs_before_cursor_pages_older_rows():
    _, tok = _admin_tok()
    a = _insert_run(trade_date="2026-08-01")
    b = _insert_run(trade_date="2026-08-02")
    c = _insert_run(trade_date="2026-08-03")
    r = client.get(f"/api/admin/runs?limit=2&before={c}", headers=_hdrs(tok))
    assert [x["id"] for x in r.json()["runs"]] == [b, a]
    r2 = client.get(f"/api/admin/runs?limit=2&before={a}", headers=_hdrs(tok))
    assert all(x["id"] < a for x in r2.json()["runs"])
    # cursor stability: a row inserted BETWEEN page fetches (a concurrent run
    # finishing) never shifts the next page — id<before is a cursor, not offset
    _insert_run(trade_date="2026-08-04")
    again = client.get(f"/api/admin/runs?limit=2&before={c}", headers=_hdrs(tok))
    assert [x["id"] for x in again.json()["runs"]] == [b, a]


def test_runs_require_admin():
    _, member_tok = _mk_member("runs-pleb@aclub.test")
    for path in ("/api/admin/runs", "/api/admin/runs/1",
                 "/api/admin/audit", "/api/admin/logins"):
        assert client.get(path, headers=_hdrs(member_tok)).status_code == 403
        assert client.get(path, headers=_hdrs()).status_code == 401


# ─── /runs/{id} detail ───────────────────────────────────────────────────────

def test_run_detail_parses_summary_and_404s():
    _, tok = _admin_tok()
    summary = {"mode": "LIVE·buy", "members": [{"member_id": 1, "orders": []}],
               "orders": [], "verification": {"verdict": "PASS"}}
    rid = _insert_run(phase="buy", mode="LIVE·buy", will_place=1,
                      verify_verdict="PASS", summary_json=json.dumps(summary))
    r = client.get(f"/api/admin/runs/{rid}", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == RUN_LIST_FIELDS | {"summary"}
    assert body["id"] == rid and body["will_place"] is True
    assert body["summary"] == summary                      # parsed, not a string
    assert client.get("/api/admin/runs/99999999", headers=_hdrs(tok)).status_code == 404


# ─── /audit browser ──────────────────────────────────────────────────────────

def test_audit_entries_newest_first_with_parsed_detail():
    _, tok = _admin_tok()
    audit("zz-alice@x.com", "zz_act_one", "t1", {"a": 1})
    audit("zz-bob@x.com", "zz_act_two", "t2", None)
    audit("zz-alice@x.com", "zz_act_two", "t3", {"b": [1, 2]})
    r = client.get("/api/admin/audit?limit=3", headers=_hdrs(tok))
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["action"] for e in entries] == ["zz_act_two", "zz_act_two", "zz_act_one"]
    assert [e["target"] for e in entries] == ["t3", "t2", "t1"]
    assert entries[0]["detail"] == {"b": [1, 2]}           # parsed JSON
    assert entries[1]["detail"] is None                    # null detail stays null
    assert entries[2]["detail"] == {"a": 1}
    for e in entries:
        assert set(e) == {"id", "ts", "actor", "action", "target", "detail"}


def test_audit_filters_are_exact_and_combinable():
    _, tok = _admin_tok()
    audit("zf-alice@x.com", "zf_act_one", "u1", None)
    audit("zf-bob@x.com", "zf_act_two", "u2", None)
    audit("zf-alice@x.com", "zf_act_two", "u3", None)
    by_action = client.get("/api/admin/audit?action=zf_act_two",
                           headers=_hdrs(tok)).json()["entries"]
    assert [e["target"] for e in by_action] == ["u3", "u2"]
    by_actor = client.get("/api/admin/audit?actor=zf-alice@x.com",
                          headers=_hdrs(tok)).json()["entries"]
    assert [e["target"] for e in by_actor] == ["u3", "u1"]
    both = client.get("/api/admin/audit?action=zf_act_two&actor=zf-alice@x.com",
                      headers=_hdrs(tok)).json()["entries"]
    assert [e["target"] for e in both] == ["u3"]
    # before= combines with the filters
    older = client.get(f"/api/admin/audit?action=zf_act_two&before={by_action[0]['id']}",
                       headers=_hdrs(tok)).json()["entries"]
    assert [e["target"] for e in older] == ["u2"]


# ─── /logins ─────────────────────────────────────────────────────────────────

def test_logins_joined_newest_first_bots_excluded():
    hid, _ = _mk_member("login-human@aclub.test")
    bid, _ = _mk_member("login-bot@aclub.test", is_bot=1)
    now = int(time.time())
    with db() as conn:
        conn.execute("INSERT INTO login_events(member_id,ts,ip,kind) VALUES(?,?,?,?)",
                     (hid, now - 60, "1.2.3.4", "signup"))
        conn.execute("INSERT INTO login_events(member_id,ts,ip,kind) VALUES(?,?,?,?)",
                     (bid, now - 30, "9.9.9.9", "login"))
        conn.execute("INSERT INTO login_events(member_id,ts,ip,kind) VALUES(?,?,?,?)",
                     (hid, now, "1.2.3.5", "login"))
    _, tok = _admin_tok()
    r = client.get("/api/admin/logins?limit=5", headers=_hdrs(tok))
    assert r.status_code == 200
    logins = r.json()["logins"]
    assert all(set(x) == {"ts", "member_id", "display_name", "email", "ip", "kind"}
               for x in logins)
    mine = [x for x in logins if x["member_id"] in (hid, bid)]
    assert [x["ip"] for x in mine] == ["1.2.3.5", "1.2.3.4"]     # newest first
    assert all(x["member_id"] != bid for x in logins)            # bot excluded
    assert mine[0]["email"] == "login-human@aclub.test"
    assert mine[0]["display_name"] == "login-human"
    assert mine[0]["kind"] == "login" and mine[1]["kind"] == "signup"
    # limit honoured
    assert len(client.get("/api/admin/logins?limit=1",
                          headers=_hdrs(tok)).json()["logins"]) == 1


# ─── manual_trigger audit on /run/{phase} ────────────────────────────────────

def _manual_trigger_rows(actor: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT target, detail_json FROM audit_log "
            "WHERE action='manual_trigger' AND actor=? ORDER BY id", (actor,)
        ).fetchall()
    return [{"target": r["target"], "detail": json.loads(r["detail_json"])} for r in rows]


def test_run_phase_audits_who_pulled_the_lever():
    email, tok = _admin_tok()
    r = client.post("/api/admin/run/open", headers=_hdrs(tok))
    assert r.status_code == 200
    rows = _manual_trigger_rows(email)
    assert rows == [{"target": "open", "detail": {"dry_run": True}}]
    # dry_run=false is recorded as such
    r = client.post("/api/admin/run/open?dry_run=false", headers=_hdrs(tok))
    assert r.status_code == 200
    assert _manual_trigger_rows(email)[-1] == {"target": "open",
                                               "detail": {"dry_run": False}}


def test_run_phase_execute_threads_manual_trigger_into_report(monkeypatch):
    """POST /run/execute stamps the run_reports row trigger='manual:<email>' —
    idle-day path so no broker is ever touched."""
    import app.calendar_us as cal
    monkeypatch.setattr(cal, "cycle_phase", lambda d: None)
    email, tok = _admin_tok()
    r = client.post("/api/admin/run/execute", headers=_hdrs(tok))
    assert r.status_code == 200
    assert "idle day" in r.json()["note"]
    assert _manual_trigger_rows(email) == [{"target": "execute",
                                            "detail": {"dry_run": True}}]
    with db() as conn:
        rep = conn.execute("SELECT * FROM run_reports ORDER BY id DESC LIMIT 1").fetchone()
    assert rep["trigger"] == f"manual:{email}"
    assert rep["phase"] == "none"
    # the audit row landed BEFORE the run wrote its report
    with db() as conn:
        arow = conn.execute(
            "SELECT ts FROM audit_log WHERE action='manual_trigger' AND actor=?",
            (email,)).fetchone()
    assert arow["ts"] <= rep["finished_at"]


def test_run_phase_audits_every_branch(monkeypatch):
    """open/power/bots/reweight/close ALL audit before dispatch — the service
    layer is stubbed to no-ops so each branch stays DB-only here (execute is
    covered by the idle-day test above)."""
    import app.routes.admin as admin_routes
    monkeypatch.setattr(admin_routes.svc, "ensure_active_session", lambda: {"id": 1})
    monkeypatch.setattr(admin_routes.power, "snapshot_all", lambda: [])
    monkeypatch.setattr(admin_routes.bots, "seed_bots", lambda: None)
    monkeypatch.setattr(admin_routes.bots, "cast_votes", lambda s: {"ok": True})
    monkeypatch.setattr(admin_routes.bots, "score_and_reweight", lambda: {"ok": True})
    monkeypatch.setattr(admin_routes.svc, "session_for_trade_date",
                        lambda d: {"id": 1, "status": "open"})
    monkeypatch.setattr(admin_routes.svc, "close_and_select",
                        lambda sid: {"id": sid, "status": "closed"})
    email, tok = _admin_tok()
    for ph in ("open", "power", "bots", "reweight", "close"):
        r = client.post(f"/api/admin/run/{ph}", headers=_hdrs(tok))
        assert r.status_code == 200, (ph, r.text)
    assert [x["target"] for x in _manual_trigger_rows(email)] == \
        ["open", "power", "bots", "reweight", "close"]


def test_run_phase_invalid_phase_400_and_unaudited():
    email, tok = _admin_tok()
    r = client.post("/api/admin/run/frobnicate", headers=_hdrs(tok))
    assert r.status_code == 400
    assert r.json()["detail"] == "phase must be open|close|execute|power|bots|reweight"
    assert _manual_trigger_rows(email) == []


# ─── config-change watch ─────────────────────────────────────────────────────

def _config_audits() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE action='config_changed' ORDER BY id"
        ).fetchall()
    return [json.loads(r["detail_json"]) for r in rows]


def _clear_fingerprint():
    with db() as conn:
        conn.execute("DELETE FROM kv WHERE key='config_fingerprint'")


def test_config_watch_first_boot_stores_silently():
    _clear_fingerprint()
    n0 = len(_config_audits())
    assert app_main.check_config_changed() is None
    from app.db import kv_get
    assert kv_get("config_fingerprint") is not None
    assert len(_config_audits()) == n0                     # first boot: silent
    # same config on the next boot → still silent
    assert app_main.check_config_changed() is None
    assert len(_config_audits()) == n0


def test_config_watch_audits_only_the_changed_keys_with_old_and_new():
    _clear_fingerprint()
    app_main.check_config_changed()                        # baseline
    old_top_n = settings.top_n
    old_cadence = settings.cycle_cadence
    new_cadence = "weekly" if old_cadence != "weekly" else "mwf"
    object.__setattr__(settings, "top_n", old_top_n + 5)
    object.__setattr__(settings, "cycle_cadence", new_cadence)
    try:
        changed = app_main.check_config_changed()
        expected = {"top_n": [old_top_n, old_top_n + 5],
                    "cycle_cadence": [old_cadence, new_cadence]}
        assert changed == expected
        last = _config_audits()[-1]
        assert last == {"changed": expected}               # ONLY the changed keys
        # fingerprint advanced: reverting the settings reports the revert
        object.__setattr__(settings, "top_n", old_top_n)
        object.__setattr__(settings, "cycle_cadence", old_cadence)
        reverted = app_main.check_config_changed()
        assert reverted == {"top_n": [old_top_n + 5, old_top_n],
                            "cycle_cadence": [new_cadence, old_cadence]}
    finally:
        object.__setattr__(settings, "top_n", old_top_n)
        object.__setattr__(settings, "cycle_cadence", old_cadence)
