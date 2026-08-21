"""Tests for the owner+cofounder admin console + morning digest.

Contract:
- require_admin: owner session 200, admin-role session 200, plain member 403,
  an API key 403 even when it belongs to the OWNER (interactive-session only),
  bots never pass regardless of role.
- GET /api/admin/panel: {state, members, cycle, money, audit}; state recomputes
  the executor's will-place gate read-only with one reason per failed gate
  (dry_run / disarmed / halt / quorum / calendar); bots are excluded from
  members[] but summarized in cycle.bots.
- Member exceptions: unverified → "email unconfirmed", disconnected →
  "not connected", fully-set-up member → [].
- Digest (services/adminview.send_digest, shared with jobs/admin_digest.py):
  recipients = non-bot owner/admin members only; email disabled → body printed,
  nothing sent; a raising mailer never propagates.

Run from backend/:  python -m pytest tests/test_admin_panel.py -q
(members inserted directly + per-test spoofed IPs, same rationale as
test_set_password.py / test_verify_email.py; mailer.send is monkeypatched.)
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# This module sorts FIRST alphabetically, so ITS imports win the process-wide
# app.config singleton — mirror the env the previously-first module
# (test_audit_fixes) set, or the whole suite's TOP_N/EXECUTION_MODE shift.
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import audit, db, init_db, set_armed  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import adminview, mailer  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()
    # Belt-and-braces: known-safe gate state, email OFF unless a test turns it on.
    object.__setattr__(settings, "resend_api_key", "")
    object.__setattr__(settings, "execution_mode", "dry_run")
    set_armed(False, actor="test-setup")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.81.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, role: str = "member", is_bot: int = 0,
               verified_at: int | None = None, opted_in: int = 0,
               alloc_value: float = 0.0, rh: dict | None = None) -> tuple[int, str]:
    """Member + settings rows (and optionally an rh_accounts row) + a session."""
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], role, hash_password("panel-pass-11"),
             verified_at, is_bot, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,?,0,0,?,?,?,?,?)",
            (mid, opted_in, settings.max_orders, settings.voting_power_base,
             "dollar", alloc_value, now),
        )
        if rh:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,status,"
                "connected_at) VALUES(?,?,?,?,?)",
                (mid, rh.get("account_number", f"ACC{mid}"), rh.get("agentic", 1),
                 rh.get("status", "connected"), now),
            )
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


def _panel(tok: str) -> dict:
    r = client.get("/api/admin/panel", headers=_hdrs(tok))
    assert r.status_code == 200
    return r.json()


# ─── require_admin matrix ────────────────────────────────────────────────────

def test_require_admin_matrix():
    _, owner_tok = _mk_member("owner@aclub.test", role="owner",
                              verified_at=int(time.time()))
    _, admin_tok = _mk_member("cofounder@aclub.test", role="admin",
                              verified_at=int(time.time()))
    _, member_tok = _mk_member("pleb@aclub.test", verified_at=int(time.time()))
    assert client.get("/api/admin/panel", headers=_hdrs(owner_tok)).status_code == 200
    assert client.get("/api/admin/panel", headers=_hdrs(admin_tok)).status_code == 200
    r = client.get("/api/admin/panel", headers=_hdrs(member_tok))
    assert r.status_code == 403
    assert r.json()["detail"] == "admin only"
    # An API key must NOT reach the console — even the OWNER's own key.
    key = client.post("/api/me/api-keys", headers=_hdrs(owner_tok),
                      json={"name": "t"}).json()["key"]
    r = client.get("/api/admin/panel", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "log in to use the admin console"


def test_bot_never_passes_even_with_admin_role():
    _, tok = _mk_member("bot-admin@aclub.test", role="admin", is_bot=1)
    r = client.get("/api/admin/panel", headers=_hdrs(tok))
    assert r.status_code == 403


def test_unauthenticated_is_401():
    assert client.get("/api/admin/panel", headers=_hdrs()).status_code == 401


# ─── panel shape + will-place recompute ──────────────────────────────────────

def test_panel_shape_and_dry_run_disarmed_reasons():
    _, tok = _mk_member("shape-admin@aclub.test", role="admin",
                        verified_at=int(time.time()))
    p = _panel(tok)
    assert set(p) == {"state", "members", "cycle", "money", "audit", "basket", "drift"}
    st = p["state"]
    assert st["execution_mode"] == "dry_run" and st["is_live"] is False
    assert st["armed"] is False and st["halt"] is False
    assert st["will_place_next"] is False
    assert "dry_run mode" in st["reasons"] and "disarmed" in st["reasons"]
    # money + audit shapes
    assert isinstance(p["money"]["per_member"], list)
    assert p["money"]["club_perf_cached"] is None      # no kv blob seeded yet
    assert isinstance(p["audit"], list) and len(p["audit"]) <= 20
    assert set(p["audit"][0]) == {"ts", "actor", "action", "target"}
    for m in p["members"]:
        assert {"id", "email", "display_name", "role", "connected", "agentic",
                "opted_in", "stake_type", "stake_value", "email_verified",
                "exceptions"} <= set(m)


def test_will_place_goes_true_when_every_gate_clears():
    mid, tok = _mk_member("live-owner@aclub.test", role="owner",
                          verified_at=int(time.time()))
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,?)", ("2026-07-28", "open", now, now + 3600, 20))
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
            "VALUES(?,?,?,?,?)", (sid, mid, "AAPL", 100, now))
    object.__setattr__(settings, "execution_mode", "live")
    set_armed(True, actor="test")
    try:
        p = _panel(tok)
        assert p["state"]["reasons"] == []
        assert p["state"]["will_place_next"] is True
        o = p["cycle"]["open"]
        assert o["id"] == sid and o["trade_date"] == "2026-07-28"
        assert o["human_voters"] == 1 and o["quorum_met"] is True
        assert o["voters"] == 1 and o["picks_so_far"] == 1
    finally:
        object.__setattr__(settings, "execution_mode", "dry_run")
        set_armed(False, actor="test")
    # halt flips it back off with its own reason (kill switch)
    from app.db import kv_set
    kv_set("halt", "1")
    try:
        st = _panel(tok)["state"]
        assert st["halt"] is True and st["will_place_next"] is False
        assert "halted (kill switch)" in st["reasons"]
    finally:
        kv_set("halt", "0")


# ─── member exceptions + bot exclusion ───────────────────────────────────────

def test_exceptions_unverified_and_disconnected_vs_fully_set_up():
    mid_u, _ = _mk_member("unverified@aclub.test")             # nothing set up
    mid_ok, _ = _mk_member("allgood@aclub.test", verified_at=int(time.time()),
                           opted_in=1, alloc_value=100.0,
                           rh={"status": "connected", "agentic": 1})
    _, tok = _mk_member("exc-admin@aclub.test", role="admin",
                        verified_at=int(time.time()))
    members = {m["id"]: m for m in _panel(tok)["members"]}
    u = members[mid_u]
    assert u["connected"] is False and u["email_verified"] is False
    assert "email unconfirmed" in u["exceptions"]
    assert "not connected" in u["exceptions"]
    assert "opted out" in u["exceptions"] and "no stake" in u["exceptions"]
    ok = members[mid_ok]
    assert ok["connected"] is True and ok["agentic"] is True
    assert ok["opted_in"] is True and ok["stake_value"] == 100.0
    assert ok["exceptions"] == []


def test_rh_error_status_is_its_own_exception():
    mid, _ = _mk_member("stranded@aclub.test", verified_at=int(time.time()),
                        opted_in=1, alloc_value=50.0,
                        rh={"status": "error", "agentic": 1})
    _, tok = _mk_member("err-admin@aclub.test", role="admin",
                        verified_at=int(time.time()))
    m = {x["id"]: x for x in _panel(tok)["members"]}[mid]
    assert m["connected"] is False
    assert "rh status error" in m["exceptions"]
    assert "not connected" not in m["exceptions"]   # stranded ≠ never-connected


def test_bots_excluded_from_members_but_counted_in_cycle():
    _mk_member("bot-voter@aclub.test", is_bot=1)
    _, tok = _mk_member("bots-admin@aclub.test", role="admin",
                        verified_at=int(time.time()))
    p = _panel(tok)
    assert all(m["email"] != "bot-voter@aclub.test" for m in p["members"])
    assert p["cycle"]["bots"]["count"] >= 2          # bot-admin + bot-voter
    assert isinstance(p["cycle"]["bots"]["voted_today"], int)


# ─── last-executed run + verify verdict ──────────────────────────────────────

def test_last_executed_by_status_and_latest_verify_verdict():
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,top_n) "
            "VALUES(?,?,?,?)", ("2026-07-25", "executed", now, 20))
        sid = cur.lastrowid
        for sym, st in (("AAPL", "placed"), ("MSFT", "placed"), ("NVDA", "error")):
            conn.execute(
                "INSERT INTO executions(session_id,member_id,symbol,side,type,"
                "dollar_amount,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (sid, 1, sym, "buy", "market", 10.0, st, now))
    audit("system", "verify_done", "2026-07-25", {"verdict": "PASS"})
    _, tok = _mk_member("verify-admin@aclub.test", role="admin",
                        verified_at=int(time.time()))
    le = _panel(tok)["cycle"]["last_executed"]
    assert le["id"] == sid and le["trade_date"] == "2026-07-25"
    assert le["by_status"] == {"placed": 2, "error": 1}
    assert le["latest_verify_verdict"] == "PASS"


# ─── the morning digest ──────────────────────────────────────────────────────

def test_digest_recipients_are_owner_and_admin_humans_only(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        mailer, "send",
        lambda to, subject, html, text: sent.append(
            {"to": to, "subject": subject, "html": html, "text": text}) or True)
    object.__setattr__(settings, "resend_api_key", "re_test_key")
    try:
        res = adminview.send_digest()
    finally:
        object.__setattr__(settings, "resend_api_key", "")
    assert res["printed"] is False and res["failed"] == []
    tos = {s["to"] for s in sent}
    with db() as conn:
        expected = {r["email"] for r in conn.execute(
            "SELECT email FROM members WHERE COALESCE(is_bot,0)=0 "
            "AND role IN ('owner','admin')").fetchall()}
    assert tos == expected == set(res["sent"])
    assert "owner@aclub.test" in tos and "cofounder@aclub.test" in tos
    assert "pleb@aclub.test" not in tos                # plain member: no digest
    assert "bot-admin@aclub.test" not in tos           # bot: never, even role=admin
    # subject + body: exceptions exist (unverified/disconnected members above)
    assert sent[0]["subject"].startswith("Concordia morning check")
    assert "need attention" in sent[0]["subject"]
    body = sent[0]["text"]
    assert body.splitlines()[2].startswith("WILL PLACE: NO")   # verdict line first
    assert "dry_run mode" in body and "Member exceptions:" in body
    assert "unverified <unverified@aclub.test>" in body
    assert "Cycle preflight:" in body and "bots:" in body
    assert "verify=PASS" in body                       # yesterday's run rides along


def test_digest_email_disabled_prints_and_sends_nothing(capsys, monkeypatch):
    called = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: called.append(a) or True)
    assert settings.email_enabled is False
    res = adminview.send_digest()
    assert res["printed"] is True and res["sent"] == [] and res["failed"] == []
    assert called == []
    out = capsys.readouterr().out
    assert "Concordia morning check" in out and "WILL PLACE" in out


def test_digest_never_raises_on_mailer_crash(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(mailer, "send", _boom)
    object.__setattr__(settings, "resend_api_key", "re_test_key")
    try:
        res = adminview.send_digest()                  # must not raise
    finally:
        object.__setattr__(settings, "resend_api_key", "")
    assert res["sent"] == [] and len(res["failed"]) >= 2


def test_job_entrypoint_always_exits_zero(capsys):
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "jobs" / "admin_digest.py"
    spec = importlib.util.spec_from_file_location("admin_digest_job", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0                             # email disabled → prints
    assert "[admin_digest]" in capsys.readouterr().out
