"""Tests for email verification (signup codes) + password reset via Resend codes.

Contract:
- RESEND_API_KEY unset → the whole feature is INERT: /signup/start answers
  code_required=false and /signup works with no code (today's exact behaviour).
- Enabled → /signup needs a live 6-digit code: missing 403, wrong 403 (attempts
  burn; after 5 misses even the CORRECT code is dead), correct → member created
  with email_verified_at set.
- Google link on a VERIFIED password account keeps the password (link only);
  on a NEVER-verified account it still wipes it (squatter rule regression).
- /forgot answers the same 200 shape whether or not the account exists (no
  enumeration); /reset with the code changes the password, kills old sessions
  (session_version bump), stamps email_verified_at; bad code → generic 403.

Run from backend/:  python -m pytest tests/test_email_verification.py -q
(mailer.send is monkeypatched — no network. Requests spoof per-test IPs via
X-Forwarded-For so the shared per-IP signup window of other modules isn't hit;
members for the google/reset tests are inserted directly for the same reason.)
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
from app.security import hash_password, make_session  # noqa: E402
from app.services import google_auth, mailer  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()
    # Belt-and-braces: the feature must be OFF unless a test turns it on.
    object.__setattr__(settings, "resend_api_key", "")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs() -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    return {"X-Forwarded-For": f"10.77.0.{_ip_counter[0]}"}


def _code_from(sent: list[dict]) -> str:
    m = re.search(r"code is: (\d{6})", sent[-1]["text"])
    assert m, f"no 6-digit code in mail text: {sent[-1]['text']!r}"
    return m.group(1)


def _wait_for_sends(sent: list[dict], n: int, timeout: float = 2.0) -> None:
    """/forgot mails from a background thread (anti-timing-oracle) — poll until
    the captured outbox reaches n before reading codes from it."""
    deadline = time.time() + timeout
    while len(sent) < n and time.time() < deadline:
        time.sleep(0.02)
    assert len(sent) >= n, f"expected {n} sent mail(s), got {len(sent)} within {timeout}s"


def _member_row(email: str):
    with db() as conn:
        return conn.execute("SELECT * FROM members WHERE lower(email)=?",
                            (email.lower(),)).fetchone()


def _mk_member(email: str, password: str | None, verified_at: int | None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, "EV Tester", "member",
             hash_password(password) if password else None, verified_at, int(time.time())),
        )
        return cur.lastrowid


def _signup_payload(email: str, **extra) -> dict:
    return {"invite_code": settings.invite_code, "email": email,
            "password": "hunter2hunter2", "display_name": "EV Member", **extra}


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


@pytest.fixture
def google_on(monkeypatch):
    old = settings.google_client_id
    object.__setattr__(settings, "google_client_id", "ev-test-client-id")
    yield
    object.__setattr__(settings, "google_client_id", old)


# ─── disabled = inert (regression: today's exact behaviour) ──────────────────

def test_email_disabled_start_says_no_code_and_signup_works_without_one():
    assert settings.email_enabled is False
    r = client.post("/api/auth/signup/start", headers=_hdrs(),
                    json={"invite_code": settings.invite_code, "email": "off@evclub.test"})
    assert r.status_code == 200 and r.json() == {"code_required": False}
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload("off@evclub.test"))
    assert r.status_code == 200
    assert _member_row("off@evclub.test")["email_verified_at"] is None
    # forgot keeps its constant shape and flags email as off
    r = client.post("/api/auth/forgot", headers=_hdrs(), json={"email": "off@evclub.test"})
    assert r.status_code == 200 and r.json() == {"ok": True, "email_enabled": False}


def test_signup_start_still_gates_the_invite():
    r = client.post("/api/auth/signup/start", headers=_hdrs(),
                    json={"invite_code": "wrong-code", "email": "gate@evclub.test"})
    assert r.status_code == 403 and r.json()["detail"] == "invalid invite code"


# ─── enabled: signup needs the emailed code ──────────────────────────────────

def test_signup_without_code_is_403(email_on):
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload("nocode@evclub.test"))
    assert r.status_code == 403
    assert r.json()["detail"] == "enter the code we emailed you"
    assert _member_row("nocode@evclub.test") is None


def test_wrong_code_burns_attempts_until_even_the_right_one_dies(email_on):
    email = "burn@evclub.test"
    r = client.post("/api/auth/signup/start", headers=_hdrs(),
                    json={"invite_code": settings.invite_code, "email": email})
    assert r.status_code == 200 and r.json() == {"code_required": True}
    assert email_on[-1]["to"] == email and email_on[-1]["subject"] == "Your Concordia code"
    code = _code_from(email_on)
    wrong = "000000" if code != "000000" else "999999"
    for _ in range(5):
        r = client.post("/api/auth/signup", headers=_hdrs(),
                        json=_signup_payload(email, email_code=wrong))
        assert r.status_code == 403
        assert r.json()["detail"] == "that code didn't match or expired — use the newest email"
    # 5 misses deleted the row: the 6th try fails even with the CORRECT code
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload(email, email_code=code))
    assert r.status_code == 403 and _member_row(email) is None


def test_correct_code_creates_a_verified_member(email_on):
    email = "happy@evclub.test"
    assert client.post("/api/auth/signup/start", headers=_hdrs(),
                       json={"invite_code": settings.invite_code, "email": email}).status_code == 200
    code = _code_from(email_on)
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload(email, email_code=code))
    assert r.status_code == 200 and r.json()["member"]["email"] == email
    row = _member_row(email)
    assert row["email_verified_at"] is not None
    # the code was consumed on the hit — replaying it can't mint anything
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload("replay@evclub.test", email_code=code))
    assert r.status_code == 403


# ─── google link vs the (relaxed) squatter rule ──────────────────────────────

def _google_login(monkeypatch, email: str, sub: str):
    monkeypatch.setattr(google_auth, "verify_id_token",
                        lambda credential: {"sub": sub, "email": email,
                                            "email_verified": True, "name": "G"})
    return client.post("/api/auth/google", headers=_hdrs(), json={"credential": "tok"})


def test_google_link_keeps_password_on_a_verified_account(google_on, monkeypatch):
    email = "g-verified@evclub.test"
    _mk_member(email, "kept-password-11", verified_at=int(time.time()) - 100)
    r = _google_login(monkeypatch, email, "sub-ev-ver-1")
    assert r.status_code == 200
    row = _member_row(email)
    assert row["password_hash"] is not None          # NOT wiped: email was proven
    assert int(row["session_version"]) == 1          # no revocation either
    # password login still works after the link — the whole point of the relax
    r = client.post("/api/auth/login", headers=_hdrs(),
                    json={"email": email, "password": "kept-password-11"})
    assert r.status_code == 200


def test_google_link_still_wipes_password_on_an_unverified_account(google_on, monkeypatch):
    email = "g-squat@evclub.test"
    _mk_member(email, "squatter-pass-11", verified_at=None)
    r = _google_login(monkeypatch, email, "sub-ev-unver-1")
    assert r.status_code == 200
    row = _member_row(email)
    assert row["password_hash"] is None              # old rule intact when unproven
    assert int(row["session_version"]) == 2          # sessions revoked
    assert row["email_verified_at"] is not None      # ...and Google verified it NOW
    r = client.post("/api/auth/login", headers=_hdrs(),
                    json={"email": email, "password": "squatter-pass-11"})
    assert r.status_code == 401


# ─── forgot / reset ──────────────────────────────────────────────────────────

def test_forgot_has_the_same_shape_for_real_and_unknown_emails(email_on):
    email = "forgot-real@evclub.test"
    _mk_member(email, "old-password-111", verified_at=None)
    r1 = client.post("/api/auth/forgot", headers=_hdrs(), json={"email": email})
    r2 = client.post("/api/auth/forgot", headers=_hdrs(),
                     json={"email": "forgot-ghost@evclub.test"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == {"ok": True, "email_enabled": True}
    # ...but only the real account actually got a code (async — let it land,
    # then a beat to be sure no ghost-mail follows)
    _wait_for_sends(email_on, 1)
    time.sleep(0.1)
    assert [m["to"] for m in email_on] == [email]


def test_reset_changes_password_kills_sessions_and_verifies_email(email_on):
    email = "reset-me@evclub.test"
    mid = _mk_member(email, "old-password-111", verified_at=None)
    tok = make_session(mid, 1)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.post("/api/auth/forgot", headers=_hdrs(), json={"email": email}).status_code == 200
    _wait_for_sends(email_on, 1)
    code = _code_from(email_on)
    r = client.post("/api/auth/reset", headers=_hdrs(),
                    json={"email": email, "code": code, "new_password": "brand-new-pass-9"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = _member_row(email)
    assert row["email_verified_at"] is not None      # inbox control proven
    # old session is dead (session_version bumped — it IS a credential change)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    # old password dead, new one works
    assert client.post("/api/auth/login", headers=_hdrs(),
                       json={"email": email, "password": "old-password-111"}).status_code == 401
    assert client.post("/api/auth/login", headers=_hdrs(),
                       json={"email": email, "password": "brand-new-pass-9"}).status_code == 200
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='reset_password' "
                            "AND actor=?", (f"member:{mid}",)).fetchone()[0] == 1
        # the code row was consumed on the hit
        assert conn.execute("SELECT COUNT(*) FROM email_codes WHERE email=?",
                            (email,)).fetchone()[0] == 0


def test_reset_with_bad_code_is_a_generic_403(email_on):
    email = "reset-bad@evclub.test"
    _mk_member(email, "old-password-111", verified_at=None)
    assert client.post("/api/auth/forgot", headers=_hdrs(), json={"email": email}).status_code == 200
    _wait_for_sends(email_on, 1)
    code = _code_from(email_on)
    wrong = "000000" if code != "000000" else "999999"
    r = client.post("/api/auth/reset", headers=_hdrs(),
                    json={"email": email, "code": wrong, "new_password": "whatever-pass-9"})
    assert r.status_code == 403 and r.json()["detail"] == "that code didn't match or expired"
    # unknown email fails with the SAME status + message (no enumeration)
    r2 = client.post("/api/auth/reset", headers=_hdrs(),
                     json={"email": "reset-ghost@evclub.test", "code": "123456",
                           "new_password": "whatever-pass-9"})
    assert r2.status_code == 403 and r2.json()["detail"] == r.json()["detail"]
    # short new_password is rejected by the model, same floor as PasswordBody
    r3 = client.post("/api/auth/reset", headers=_hdrs(),
                     json={"email": email, "code": code, "new_password": "short"})
    assert r3.status_code == 422


# ─── gate findings: per-IP send window + failed-send code preservation ───────

def test_signup_start_per_ip_window_stops_distinct_address_spray(email_on):
    """One invite-holding IP must NOT be able to spray codes at unlimited
    DISTINCT inboxes — the per-address window alone never fills (gate finding)."""
    hdrs = {"X-Forwarded-For": "10.77.200.1"}   # FIXED ip across the spray
    for i in range(12):
        r = client.post("/api/auth/signup/start", headers=hdrs,
                        json={"invite_code": settings.invite_code,
                              "email": f"spray-{i}@evclub.test"})
        assert r.status_code == 200, f"send {i+1} unexpectedly blocked"
    r = client.post("/api/auth/signup/start", headers=hdrs,
                    json={"invite_code": settings.invite_code,
                          "email": "spray-final@evclub.test"})
    assert r.status_code == 429
    assert len(email_on) == 12   # the 13th never sent


def test_failed_send_preserves_the_previous_code(email_on, monkeypatch):
    """A mail hiccup on re-request must not destroy the code already in the
    member's inbox — codes persist only after a successful send."""
    email = "keepcode@evclub.test"
    assert client.post("/api/auth/signup/start", headers=_hdrs(),
                       json={"invite_code": settings.invite_code, "email": email}).status_code == 200
    code = _code_from(email_on)
    monkeypatch.setattr(mailer, "send", lambda *a, **k: False)   # outage
    r = client.post("/api/auth/signup/start", headers=_hdrs(),
                    json={"invite_code": settings.invite_code, "email": email})
    assert r.status_code == 502
    # the FIRST code still works — the failed resend didn't clobber it
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload(email, email_code=code))
    assert r.status_code == 200
    assert _member_row(email)["email_verified_at"] is not None


def test_duplicate_account_409_does_not_burn_the_code(email_on):
    """409 "account exists" must surface BEFORE the code is consumed, so the
    member keeps a working code (e.g. to realize they should log in instead)."""
    email = "dupe-keep@evclub.test"
    _mk_member(email, "already-here-11", verified_at=None)
    assert client.post("/api/auth/signup/start", headers=_hdrs(),
                       json={"invite_code": settings.invite_code, "email": email}).status_code == 200
    code = _code_from(email_on)
    r = client.post("/api/auth/signup", headers=_hdrs(),
                    json=_signup_payload(email, email_code=code))
    assert r.status_code == 409
    with db() as conn:   # the code row survived the 409
        assert conn.execute("SELECT COUNT(*) FROM email_codes WHERE email=? AND purpose='signup'",
                            (email,)).fetchone()[0] == 1
