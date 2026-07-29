"""Tests for the self-service email change (POST /me/email/start + /confirm).

Contract:
- start: session-only (api-key 403), mails a 6-digit code to the NEW address,
  409 when the address already has an account (own row included), 503 when
  email is off entirely, and the response NEVER carries the code (house rule).
- confirm: the right code swaps members.email and the account stays verified
  (the code just proved the new inbox); wrong code 403 and nothing changes;
  api-key 403.

Run from backend/:  python -m pytest tests/test_email_change.py -q
(mailer.send is monkeypatched — no network. Requests spoof per-test IPs via
X-Forwarded-For so shared per-IP windows of other modules aren't hit.)
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
from app.services import apikeys, mailer  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()
    object.__setattr__(settings, "resend_api_key", "")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(token: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop)
    + optional bearer auth."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.78.0.{_ip_counter[0]}"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _code_from(sent: list[dict]) -> str:
    m = re.search(r"code is: (\d{6})", sent[-1]["text"])
    assert m, f"no 6-digit code in mail text: {sent[-1]['text']!r}"
    return m.group(1)


def _mk_member(email: str) -> tuple[int, str]:
    """Insert a member directly; returns (id, session token)."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, "EC Tester", "member", hash_password("hunter2hunter2"),
             int(time.time()), int(time.time())),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)


def _member_row(mid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()


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


# ─── the flow ────────────────────────────────────────────────────────────────

def test_happy_path_swaps_the_email_and_stays_verified(email_on):
    mid, tok = _mk_member("old-addr@ecclub.test")
    r = client.post("/api/me/email/start", headers=_hdrs(tok),
                    json={"new_email": "New-Addr@ecclub.test"})
    # the response says ONLY that it sent — the code travels by email, never here
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert email_on[-1]["to"] == "new-addr@ecclub.test"   # normalized + mailed to the NEW inbox
    code = _code_from(email_on)
    r = client.post("/api/me/email/confirm", headers=_hdrs(tok),
                    json={"new_email": "new-addr@ecclub.test", "code": code})
    assert r.status_code == 200
    assert r.json()["member"]["email"] == "new-addr@ecclub.test"
    assert r.json()["member"]["email_verified"] is True   # never lost by changing
    row = _member_row(mid)
    assert row["email"] == "new-addr@ecclub.test" and row["email_verified_at"] is not None
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='email_change' "
                            "AND actor=?", (f"member:{mid}",)).fetchone()[0] == 1
        # the code was consumed on the hit
        assert conn.execute("SELECT COUNT(*) FROM email_codes WHERE email=?",
                            ("new-addr@ecclub.test",)).fetchone()[0] == 0
    # the session survives (no credential changed) and password login moves too
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.post("/api/auth/login", headers=_hdrs(),
                       json={"email": "new-addr@ecclub.test",
                             "password": "hunter2hunter2"}).status_code == 200


def test_wrong_code_is_403_and_changes_nothing(email_on):
    mid, tok = _mk_member("keeps-addr@ecclub.test")
    assert client.post("/api/me/email/start", headers=_hdrs(tok),
                       json={"new_email": "wanted@ecclub.test"}).status_code == 200
    code = _code_from(email_on)
    wrong = "000000" if code != "000000" else "999999"
    r = client.post("/api/me/email/confirm", headers=_hdrs(tok),
                    json={"new_email": "wanted@ecclub.test", "code": wrong})
    assert r.status_code == 403
    assert _member_row(mid)["email"] == "keeps-addr@ecclub.test"


def test_taken_email_is_409_at_start(email_on):
    _mk_member("occupied@ecclub.test")
    _, tok = _mk_member("wants-it@ecclub.test")
    r = client.post("/api/me/email/start", headers=_hdrs(tok),
                    json={"new_email": "occupied@ecclub.test"})
    assert r.status_code == 409
    # your OWN current address counts as taken too (a no-op "change")
    r = client.post("/api/me/email/start", headers=_hdrs(tok),
                    json={"new_email": "wants-it@ecclub.test"})
    assert r.status_code == 409
    assert email_on == []   # neither 409 sent any mail


def test_api_key_auth_is_403_on_both_steps(email_on):
    mid, _ = _mk_member("keyed@ecclub.test")
    key = apikeys.create_key(mid, "t")["key"]
    for path, body in (("/api/me/email/start", {"new_email": "via-key@ecclub.test"}),
                       ("/api/me/email/confirm", {"new_email": "via-key@ecclub.test",
                                                  "code": "123456"})):
        r = client.post(path, headers=_hdrs(key), json=body)
        assert r.status_code == 403, path
    assert _member_row(mid)["email"] == "keyed@ecclub.test"
    assert email_on == []


def test_bad_format_is_422_and_email_off_is_503():
    _, tok = _mk_member("format@ecclub.test")
    r = client.post("/api/me/email/start", headers=_hdrs(tok), json={"new_email": "not-an-email"})
    assert r.status_code == 422
    # feature off (no key, no demo): fail closed — nothing to prove the inbox with
    assert settings.email_enabled is False
    r = client.post("/api/me/email/start", headers=_hdrs(tok),
                    json={"new_email": "fine@ecclub.test"})
    assert r.status_code == 503
