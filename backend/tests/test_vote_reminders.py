"""Ballot lifecycle emails (services/notify: open + closing-soon reminders).

Contract under test:
- ballot-open email goes to every human, verified, opted-in member with
  reminders on — and to nobody else (bots, unverified, opted-out, deleted)
- closing-soon goes ONLY to members who have not voted in the session
- each kind sends at most once per session (kv stamp), so catch-up timer
  re-runs cannot double-mail the club
- the /me/email-reminders toggle flips the column WITHOUT touching
  updated_at (that timestamp doubles as the stake-freeze / demo buy-in clock)
- reminder emails are suppressed wholesale in demo mode

Run from backend/:  python -m pytest tests/test_vote_reminders.py -q
"""
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import notify  # noqa: E402

client = TestClient(app)


def setup_module(_):
    init_db()


def _mk_member(email: str, *, verified=True, opted_in=1, reminders=1, is_bot=0):
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("reminder-pass-11"),
             now if verified else None, is_bot, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,email_reminders,"
            "updated_at) VALUES(?,?,0,0,25,0,'dollar',100.0,?,?)",
            (mid, opted_in, reminders, now),
        )
    return mid


def _mk_session(trade_date: str) -> dict:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,20)", (trade_date, "open", now, now + 6 * 3600),
        )
        sid = cur.lastrowid
    return {"id": sid, "trade_date": trade_date, "status": "open", "closes_at": now + 6 * 3600}


def _capture(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "_deliver",
                        lambda to, subject, body, out: (sent.append((to, subject)),
                                                        out["sent"].append(to)))
    return sent


def test_open_email_targets_exactly_the_reminderable(monkeypatch):
    _mk_member("open-yes@aclub.test")
    _mk_member("open-bot@aclub.test", is_bot=1)
    _mk_member("open-unverified@aclub.test", verified=False)
    _mk_member("open-out@aclub.test", opted_in=0)
    _mk_member("open-optout@aclub.test", reminders=0)
    sent = _capture(monkeypatch)
    session = _mk_session("2031-09-01")
    res = notify.send_ballot_open_emails(session)
    assert res["skipped"] is None
    assert [t for t, _ in sent] == ["open-yes@aclub.test"]


def test_open_email_sends_once_per_session(monkeypatch):
    _mk_member("once@bclub.test")
    sent = _capture(monkeypatch)
    session = _mk_session("2031-09-02")
    notify.send_ballot_open_emails(session)
    res2 = notify.send_ballot_open_emails(session)
    assert res2["skipped"] == "already sent"
    assert len([t for t, _ in sent if t == "once@bclub.test"]) == 1


def test_closing_soon_skips_members_who_voted(monkeypatch):
    voted = _mk_member("voted@cclub.test")
    _mk_member("lazy@cclub.test")
    session = _mk_session("2031-09-03")
    with db() as conn:
        conn.execute("INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
                     "VALUES(?,?,?,?,?)", (session["id"], voted, "AAPL", 100, int(time.time())))
    sent = _capture(monkeypatch)
    notify.send_closing_soon_emails(session)
    recipients = [t for t, _ in sent]
    assert "lazy@cclub.test" in recipients          # non-voter nudged
    assert "voted@cclub.test" not in recipients     # voter left alone


def test_demo_mode_sends_nothing(monkeypatch):
    _mk_member("demo@dclub.test")
    sent = _capture(monkeypatch)
    session = _mk_session("2031-09-04")
    from types import SimpleNamespace
    shim = SimpleNamespace(demo_mode=True, member_run_emails=True)
    monkeypatch.setattr(notify, "settings", shim)
    res = notify.send_ballot_open_emails(session)
    assert res["skipped"] == "demo mode"
    assert sent == []


def test_toggle_endpoint_leaves_updated_at_alone():
    mid = _mk_member("toggle@eclub.test")
    tok = make_session(mid, 1)
    with db() as conn:
        before = conn.execute("SELECT updated_at, email_reminders FROM member_settings "
                              "WHERE member_id=?", (mid,)).fetchone()
    r = client.put("/api/me/email-reminders", json={"on": False},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["member"]["settings"]["email_reminders"] == 0
    with db() as conn:
        after = conn.execute("SELECT updated_at, email_reminders FROM member_settings "
                             "WHERE member_id=?", (mid,)).fetchone()
    assert after["email_reminders"] == 0
    assert after["updated_at"] == before["updated_at"]


# ─── pick rationale (thesis) ────────────────────────────────────────────────

def _seed_universe(*syms):
    with db() as conn:
        for s in syms:
            conn.execute("INSERT OR IGNORE INTO universe(symbol,name,active) VALUES(?,?,1)", (s, s))


def test_thesis_round_trips_and_is_sanitized():
    from app.services import voting
    mid = _mk_member("thesis@fclub.test")
    _seed_universe("AAPL", "MSFT")
    session = _mk_session("2031-09-05")
    stored = voting.set_member_allocations(session["id"], mid, [
        {"symbol": "AAPL", "weight": 60, "thesis": "  services\x00 flywheel\n\n keeps  compounding "},
        {"symbol": "MSFT", "weight": 40, "thesis": "   "},
    ])
    by_sym = {r["symbol"]: r for r in stored}
    assert by_sym["AAPL"]["thesis"] == "services flywheel keeps compounding"
    assert by_sym["MSFT"]["thesis"] is None          # whitespace-only → NULL, not ''
    picks = voting.member_picks(session["id"], mid)
    assert {p["symbol"]: p["thesis"] for p in picks}["AAPL"] == "services flywheel keeps compounding"


def test_theses_surface_on_tally_humans_only():
    from app.services import voting
    human = _mk_member("why@gclub.test")
    bot = _mk_member("bot-why@gclub.test", is_bot=1)
    _seed_universe("NVDA")
    session = _mk_session("2031-09-06")
    voting.set_member_allocations(session["id"], human, [
        {"symbol": "NVDA", "weight": 100, "thesis": "datacenter capex supercycle"}])
    voting.set_member_allocations(session["id"], bot, [
        {"symbol": "NVDA", "weight": 100, "thesis": "bot chatter must stay hidden"}])
    pub = voting.session_public(voting.get_session(session["id"]), human)
    row = next(r for r in pub["tally"] if r["symbol"] == "NVDA")
    texts = [t["text"] for t in row.get("theses", [])]
    assert "datacenter capex supercycle" in texts
    assert "bot chatter must stay hidden" not in texts


# ─── minted invites ─────────────────────────────────────────────────────────

def test_minted_invite_lifecycle():
    from app.services import invites
    row = invites.mint(None, "campus batch", expires_days=7, max_uses=2)
    code = row["code"]
    assert invites.check(code) is True
    assert invites.redeem(code) == code
    assert invites.redeem(code) == code          # second use of a 2-use code
    assert invites.redeem(code) is None          # third refused
    assert invites.check(code) is False


def test_revoked_and_expired_invites_refuse():
    from app.services import invites
    r1 = invites.mint(None, "revoke me")
    assert invites.revoke(r1["id"]) is True
    assert invites.check(r1["code"]) is False
    assert invites.redeem(r1["code"]) is None
    r2 = invites.mint(None, "expired")
    with db() as conn:
        conn.execute("UPDATE invites SET expires_at=? WHERE id=?", (int(time.time()) - 60, r2["id"]))
    assert invites.check(r2["code"]) is False


def test_legacy_env_code_still_admits():
    from app.services import invites
    assert invites.check(settings.invite_code) is True
    assert invites.redeem(settings.invite_code) == settings.invite_code


def test_signup_with_minted_code_records_attribution():
    from app.services import invites
    row = invites.mint(None, "attribution", max_uses=1)
    r = client.post("/api/auth/signup/start", json={"invite_code": row["code"],
                                               "email": "minted@hclub.test"})
    assert r.status_code == 200
    r = client.post("/api/auth/signup", json={
        "invite_code": row["code"], "email": "minted@hclub.test",
        "display_name": "Minted", "password": "minted-pass-11", "consent": True,
    })
    assert r.status_code == 200, r.text
    with db() as conn:
        got = conn.execute("SELECT invite_code_used FROM members WHERE email=?",
                           ("minted@hclub.test",)).fetchone()
        used = conn.execute("SELECT uses FROM invites WHERE id=?", (row["id"],)).fetchone()
    assert got["invite_code_used"] == row["code"]
    assert used["uses"] == 1
    # the code is spent — a second signup with it is refused at the gate
    r2 = client.post("/api/auth/signup/start", json={"invite_code": row["code"],
                                                "email": "second@hclub.test"})
    assert r2.status_code == 403


def test_admin_invite_routes_gated():
    r = client.get("/api/admin/invites")
    assert r.status_code in (401, 403)
