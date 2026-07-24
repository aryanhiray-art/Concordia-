"""Tests for Google sign-in (/api/auth/google) + club-agreement consent.

Contract:
- existing member (matched by verified email, case-insensitive) → logs in,
  invite/consent ignored; the Google identity is linked idempotently.
- new member → needs a valid invite_code AND consent===true; creates the member
  (display_name from the sanitized Google profile name) + consent + identity rows.
- bad/unverified token → 401; GOOGLE_CLIENT_ID unset → 503.
- password signup: consent optional in dev, REQUIRED true in prod; recorded when given.

Run from backend/:  python -m pytest tests/test_google_auth.py -q
(uses a throwaway temp DB; google_auth.verify_id_token is monkeypatched — no network).
"""
import os
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
from app.routes import auth as auth_routes  # noqa: E402
from app.services import google_auth  # noqa: E402

TEST_AUD = "test-google-client-id.apps.googleusercontent.com"

client = TestClient(app)   # no context manager → no startup event (init_db below)

_old_client_id = None


def setup_module(_):
    global _old_client_id
    init_db()
    # settings may have been constructed by an earlier test file (before this
    # module could set env vars), so force the value on the frozen instance.
    _old_client_id = settings.google_client_id
    object.__setattr__(settings, "google_client_id", TEST_AUD)


def teardown_module(_):
    object.__setattr__(settings, "google_client_id", _old_client_id)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _info(email: str, sub: str, name: str = "Test User") -> dict:
    return {"sub": sub, "email": email, "email_verified": True, "name": name}


def _use_token(monkeypatch, info: dict) -> None:
    """Stand in for google_auth.verify_id_token — returns `info` for any credential."""
    monkeypatch.setattr(google_auth, "verify_id_token", lambda credential: dict(info))


def _google(payload: dict):
    return client.post("/api/auth/google", json={"credential": "tok", **payload})


def _member_row(email: str):
    with db() as conn:
        return conn.execute("SELECT * FROM members WHERE lower(email)=?", (email.lower(),)).fetchone()


def _consents(mid: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM consents WHERE member_id=?", (mid,)).fetchall()
    return [dict(r) for r in rows]


def _identities(mid: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM oauth_identities WHERE member_id=?", (mid,)).fetchall()
    return [dict(r) for r in rows]


@pytest.fixture
def prod_env():
    old = settings.app_env
    object.__setattr__(settings, "app_env", "prod")
    yield
    object.__setattr__(settings, "app_env", old)


# ─── /api/auth/google ────────────────────────────────────────────────────────

def test_new_member_with_invite_and_consent_creates_everything(monkeypatch):
    _use_token(monkeypatch, _info("g-new@gclub.test", "sub-new-1", name="Ada  Lovelace\n"))
    r = _google({"invite_code": settings.invite_code, "consent": True})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["member"]["email"] == "g-new@gclub.test"
    row = _member_row("g-new@gclub.test")
    assert row is not None and row["password_hash"] is None
    assert row["display_name"] == "Ada Lovelace"          # sanitized Google name
    mid = row["id"]
    cons = _consents(mid)
    assert len(cons) == 1 and cons[0]["kind"] == "club_agreement_v1"
    idents = _identities(mid)
    assert len(idents) == 1
    assert idents[0]["provider"] == "google" and idents[0]["subject"] == "sub-new-1"
    assert idents[0]["email"] == "g-new@gclub.test"
    # member payload never leaks the password_hash column
    assert "password_hash" not in body["member"]


def test_existing_email_logs_in_without_invite_and_links_identity(monkeypatch):
    # existing password account, stored (as ever) lowercase; token email differs in case
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?)",
            ("g-existing@gclub.test", "Existing", "member", "pbkdf2$1$x$y", int(time.time())),
        )
        mid = cur.lastrowid
    _use_token(monkeypatch, _info("G-Existing@GClub.Test", "sub-exist-1"))
    r = _google({})                                        # no invite, no consent
    assert r.status_code == 200
    assert r.json()["member"]["id"] == mid                 # logged into the SAME account
    assert _member_row("g-existing@gclub.test")["id"] == mid
    idents = _identities(mid)
    assert len(idents) == 1 and idents[0]["subject"] == "sub-exist-1"
    # second sign-in is idempotent: still exactly one identity row, still logs in
    assert _google({}).status_code == 200
    assert len(_identities(mid)) == 1
    # login was NOT treated as a signup: no consent row was fabricated
    assert _consents(mid) == []


def test_new_member_without_invite_is_403(monkeypatch):
    _use_token(monkeypatch, _info("g-noinvite@gclub.test", "sub-noinv-1"))
    r = _google({"consent": True})                         # missing invite entirely
    assert r.status_code == 403
    assert r.json()["detail"] == "invite code required or invalid"
    r2 = _google({"invite_code": "wrong-code", "consent": True})
    assert r2.status_code == 403
    assert _member_row("g-noinvite@gclub.test") is None


def test_new_member_without_consent_is_403(monkeypatch):
    _use_token(monkeypatch, _info("g-noconsent@gclub.test", "sub-nocon-1"))
    for consent in ({}, {"consent": False}):
        r = _google({"invite_code": settings.invite_code, **consent})
        assert r.status_code == 403
        assert r.json()["detail"] == "you must accept the club agreement"
    assert _member_row("g-noconsent@gclub.test") is None


def test_bad_token_is_401(monkeypatch):
    def _boom(credential):
        raise ValueError("google sign-in token rejected")
    monkeypatch.setattr(google_auth, "verify_id_token", _boom)
    r = _google({"invite_code": settings.invite_code, "consent": True})
    assert r.status_code == 401
    assert r.json()["detail"] == "google sign-in token rejected"


def test_unverified_email_never_creates_member(monkeypatch):
    """Route-level belt-and-braces: even if the verifier were fooled, an
    unverified email must not mint (or match) a member."""
    _use_token(monkeypatch, {"sub": "sub-unver-1", "email": "g-unverified@gclub.test",
                             "email_verified": "false", "name": "Sneaky"})
    r = _google({"invite_code": settings.invite_code, "consent": True})
    assert r.status_code == 401
    assert _member_row("g-unverified@gclub.test") is None


def test_unconfigured_google_client_id_is_503(monkeypatch):
    object.__setattr__(settings, "google_client_id", "")
    try:
        r = _google({"invite_code": settings.invite_code, "consent": True})
    finally:
        object.__setattr__(settings, "google_client_id", TEST_AUD)
    assert r.status_code == 503
    assert r.json()["detail"] == "google sign-in not configured"


# ─── password signup consent ─────────────────────────────────────────────────

def _signup_payload(email: str, consent=None) -> dict:
    p = {"invite_code": settings.invite_code, "email": email,
         "password": "hunter2hunter2", "display_name": "PW Member"}
    if consent is not None:
        p["consent"] = consent
    return p


def test_prod_password_signup_requires_consent(prod_env):
    r = client.post("/api/auth/signup", json=_signup_payload("pw-prod-nocon@gclub.test"))
    assert r.status_code == 403
    assert r.json()["detail"] == "you must accept the club agreement"
    assert _member_row("pw-prod-nocon@gclub.test") is None

    r2 = client.post("/api/auth/signup",
                     json=_signup_payload("pw-prod-ok@gclub.test", consent=True))
    assert r2.status_code == 200
    mid = _member_row("pw-prod-ok@gclub.test")["id"]
    cons = _consents(mid)
    assert len(cons) == 1 and cons[0]["kind"] == "club_agreement_v1"


def test_dev_password_signup_still_works_without_consent():
    """Old clients (no consent field) keep working outside prod — no consent row."""
    r = client.post("/api/auth/signup", json=_signup_payload("pw-dev-nocon@gclub.test"))
    assert r.status_code == 200
    assert _consents(_member_row("pw-dev-nocon@gclub.test")["id"]) == []
    # ...and when dev DOES send consent, it is recorded
    r2 = client.post("/api/auth/signup",
                     json=_signup_payload("pw-dev-con@gclub.test", consent=True))
    assert r2.status_code == 200
    cons = _consents(_member_row("pw-dev-con@gclub.test")["id"])
    assert len(cons) == 1 and cons[0]["kind"] == "club_agreement_v1"


# ─── real verifier checks (network stubbed at the requests layer) ─────────────

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _tokeninfo(monkeypatch, payload: dict, status: int = 200) -> None:
    monkeypatch.setattr(google_auth.requests, "get",
                        lambda url, params=None, timeout=None: _Resp(status, payload))


def _good_claims(**over) -> dict:
    base = {"aud": TEST_AUD, "iss": "accounts.google.com", "email": "v@gclub.test",
            "email_verified": "true", "sub": "sub-v-1", "name": "V",
            "exp": str(int(time.time()) + 600)}
    base.update(over)
    return base


def test_verify_id_token_accepts_good_claims(monkeypatch):
    _tokeninfo(monkeypatch, _good_claims(email="V@GClub.Test"))
    out = google_auth.verify_id_token("tok")
    assert out == {"sub": "sub-v-1", "email": "v@gclub.test",
                   "email_verified": True, "name": "V"}


@pytest.mark.parametrize("bad", [
    dict(aud="someone-elses-client-id"),
    dict(iss="https://evil.example"),
    dict(email_verified="false"),
    dict(exp=str(int(time.time()) - 10)),
    dict(sub=""),
    dict(email=""),
])
def test_verify_id_token_rejects_bad_claims(monkeypatch, bad):
    _tokeninfo(monkeypatch, _good_claims(**bad))
    with pytest.raises(ValueError):
        google_auth.verify_id_token("tok")


def test_verify_id_token_rejects_non_200(monkeypatch):
    _tokeninfo(monkeypatch, {"error": "invalid_token"}, status=400)
    with pytest.raises(ValueError):
        google_auth.verify_id_token("tok")


# ─── DEMO_MODE smoke (piggybacks on this file's temp-DB env setup) ───────────

def test_demo_mode_forces_is_live_false_and_returns_demo_client(monkeypatch):
    from app.config import settings
    from app.robinhood import vault
    from app.robinhood.demo import DemoClient
    object.__setattr__(settings, "demo_mode", True)
    object.__setattr__(settings, "execution_mode", "live")
    try:
        assert settings.is_live is False          # demo can NEVER trade
        c = vault.ensure_client(999999)           # no rh_accounts row needed
        assert isinstance(c, DemoClient)
        port = c.get_portfolio(c.DEMO_ACCOUNT)["data"]
        assert port["buying_power"]["buying_power"] == 100.0
        q = c.get_quotes(["AAPL"])["data"]["results"][0]["quote"]
        assert q["symbol"] == "AAPL" and q["last_trade_price"] > 0
    finally:
        object.__setattr__(settings, "demo_mode", False)
        object.__setattr__(settings, "execution_mode", "dry_run")
