"""Tests for post-login email confirmation of GRANDFATHERED members.

Contract:
- load_member exposes email_verified + needs_email_verification:
  needs_email_verification is true only when email is ENABLED, the row has no
  email_verified_at, and the member isn't a bot. With email OFF it MUST be
  false — the UI must never gate a member behind a code that can't be sent.
- POST /api/me/verify-email/start mails a code to the member's own address
  (signup-style rate windows); already-verified → {sent:false,
  already_verified:true}; email off → {sent:false}.
- POST /api/me/verify-email {code} stamps email_verified_at, audits
  "email_verified", returns the refreshed member; bad code → 403 and burns an
  email_codes attempt.
- Both endpoints are interactive-session-only: an API key gets 403.

Run from backend/:  python -m pytest tests/test_verify_email.py -q
(mailer.send is monkeypatched — no network. Requests spoof per-test IPs via
X-Forwarded-For so shared per-IP windows of other modules aren't hit; members
are inserted directly, same rationale as test_set_password.py.)
"""
import os
import re
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routes.deps import load_member  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import mailer  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()
    # Belt-and-braces: the feature must be OFF unless a test turns it on.
    object.__setattr__(settings, "resend_api_key", "")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.78.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _code_from(sent: list[dict]) -> str:
    m = re.search(r"code is: (\d{6})", sent[-1]["text"])
    assert m, f"no 6-digit code in mail text: {sent[-1]['text']!r}"
    return m.group(1)


def _mk_member(email: str, verified_at: int | None = None, is_bot: int = 0) -> tuple[int, str]:
    """Grandfathered-style member row (password login, no email_verified_at
    unless given) + settings row + a live session token."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, "VE Tester", "member", hash_password("grandpa-pass-11"),
             verified_at, is_bot, int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,updated_at) VALUES(?,0,0,0,?,?,?)",
            (mid, settings.max_orders, settings.voting_power_base, int(time.time())),
        )
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


@pytest.fixture
def email_on(monkeypatch):
    """Turn the feature on and capture outgoing mail (no network)."""
    object.__setattr__(settings, "resend_api_key", "re_test_key")
    sent: list[dict] = []
    monkeypatch.setattr(
        mailer, "send",
        lambda to, subject, html, text: sent.append(
            {"to": to, "subject": subject, "html": html, "text": text}) or True,
    )
    yield sent
    object.__setattr__(settings, "resend_api_key", "")


# ─── the load_member flags ───────────────────────────────────────────────────

def test_unverified_member_needs_verification_only_when_email_is_on(email_on):
    mid, tok = _mk_member("flag-on@veclub.test")
    me = client.get("/api/me", headers=_hdrs(tok)).json()["member"]
    assert me["email_verified"] is False
    assert me["needs_email_verification"] is True


def test_unverified_member_is_not_gated_when_email_is_off():
    """Email OFF ⇒ needs_email_verification MUST be false — the UI would
    otherwise trap the member behind a code that can never be sent."""
    assert settings.email_enabled is False
    mid, tok = _mk_member("flag-off@veclub.test")
    me = client.get("/api/me", headers=_hdrs(tok)).json()["member"]
    assert me["email_verified"] is False
    assert me["needs_email_verification"] is False


def test_verified_member_and_bot_are_never_gated(email_on):
    mid_v, _ = _mk_member("flag-ver@veclub.test", verified_at=int(time.time()) - 100)
    m = load_member(mid_v)
    assert m["email_verified"] is True and m["needs_email_verification"] is False
    mid_b, _ = _mk_member("flag-bot@veclub.test", is_bot=1)
    b = load_member(mid_b)
    assert b["needs_email_verification"] is False


# ─── the happy path ──────────────────────────────────────────────────────────

def test_start_sends_and_code_verifies_flips_flag_and_audits(email_on):
    email = "happy@veclub.test"
    mid, tok = _mk_member(email)
    r = client.post("/api/me/verify-email/start", headers=_hdrs(tok))
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert email_on[-1]["to"] == email and email_on[-1]["subject"] == "Your Concordia code"
    code = _code_from(email_on)
    r = client.post("/api/me/verify-email", headers=_hdrs(tok), json={"code": code})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["member"]["email_verified"] is True
    assert body["member"]["needs_email_verification"] is False
    with db() as conn:
        assert conn.execute("SELECT email_verified_at FROM members WHERE id=?",
                            (mid,)).fetchone()[0] is not None
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='email_verified' "
                            "AND actor=?", (f"member:{mid}",)).fetchone()[0] == 1
        # the code row was consumed on the hit
        assert conn.execute("SELECT COUNT(*) FROM email_codes WHERE email=? AND purpose='verify'",
                            (email,)).fetchone()[0] == 0


def test_already_verified_start_is_a_noop(email_on):
    _, tok = _mk_member("already@veclub.test", verified_at=int(time.time()) - 100)
    r = client.post("/api/me/verify-email/start", headers=_hdrs(tok))
    assert r.status_code == 200 and r.json() == {"sent": False, "already_verified": True}
    assert email_on == []   # nothing mailed


def test_start_with_email_off_says_not_sent():
    assert settings.email_enabled is False
    _, tok = _mk_member("off@veclub.test")
    r = client.post("/api/me/verify-email/start", headers=_hdrs(tok))
    assert r.status_code == 200 and r.json() == {"sent": False}


# ─── failure paths ───────────────────────────────────────────────────────────

def test_bad_code_is_403_and_burns_an_attempt(email_on):
    email = "badcode@veclub.test"
    mid, tok = _mk_member(email)
    assert client.post("/api/me/verify-email/start", headers=_hdrs(tok)).status_code == 200
    code = _code_from(email_on)
    wrong = "000000" if code != "000000" else "999999"
    r = client.post("/api/me/verify-email", headers=_hdrs(tok), json={"code": wrong})
    assert r.status_code == 403
    assert r.json()["detail"] == "that code didn't match or expired — use the newest email"
    with db() as conn:
        assert conn.execute("SELECT attempts FROM email_codes WHERE email=? AND purpose='verify'",
                            (email,)).fetchone()["attempts"] == 1
        assert conn.execute("SELECT email_verified_at FROM members WHERE id=?",
                            (mid,)).fetchone()[0] is None
    # the real code still works after a miss
    r = client.post("/api/me/verify-email", headers=_hdrs(tok), json={"code": code})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_mail_outage_is_502(email_on, monkeypatch):
    _, tok = _mk_member("outage@veclub.test")
    monkeypatch.setattr(mailer, "send", lambda *a, **k: False)
    r = client.post("/api/me/verify-email/start", headers=_hdrs(tok))
    assert r.status_code == 502
    assert r.json()["detail"] == "couldn't send the code — try again in a minute"


def test_api_key_cannot_drive_verification(email_on):
    """Mirrors the set-password rationale: a leaked API key must not be able to
    mint account-level trust for itself."""
    mid, tok = _mk_member("apikey@veclub.test")
    key = client.post("/api/me/api-keys", headers=_hdrs(tok),
                      json={"name": "t"}).json()["key"]
    for path, payload in (("/api/me/verify-email/start", None),
                          ("/api/me/verify-email", {"code": "123456"})):
        r = client.post(path, headers={"Authorization": f"Bearer {key}"}, json=payload)
        assert r.status_code == 403, path
        assert r.json()["detail"] == "log in to verify your email"
    with db() as conn:
        assert conn.execute("SELECT email_verified_at FROM members WHERE id=?",
                            (mid,)).fetchone()[0] is None


def test_start_respects_the_per_address_send_window(email_on):
    _, tok = _mk_member("window@veclub.test")
    for _ in range(3):
        assert client.post("/api/me/verify-email/start",
                           headers=_hdrs(tok)).status_code == 200
    r = client.post("/api/me/verify-email/start", headers=_hdrs(tok))
    assert r.status_code == 429
    assert len(email_on) == 3   # the 4th never sent
