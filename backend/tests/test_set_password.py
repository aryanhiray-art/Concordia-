"""Tests for PUT /api/me/password set-vs-change semantics.

Contract:
- Google-only member (password_hash NULL) may SET a first password with no
  current_password; afterwards email+password login works AND the pre-existing
  session survives (no session_version bump on set — nothing to revoke).
- Password accounts keep the strict change flow: wrong OR missing
  current_password is 403.
- Both paths enforce the 8-char minimum on new_password (422 from the model).
- load_member exposes has_password (bool) without ever leaking the hash.

Run from backend/:  python -m pytest tests/test_set_password.py -q
(members are inserted directly — signup shares one in-memory per-IP rate window
with the other test modules, and 'testclient' would blow through it).
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _mk_member(email: str, password: str | None) -> tuple[int, str]:
    """Member + settings rows and a live session token. password=None mirrors a
    Google-only account (created via /google, or hash NULLed by the squatter
    protection); the token then plays the role of one minted by Google sign-in."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,created_at) "
            "VALUES(?,?,?,?,?)",
            (email, "PW Tester", "member",
             hash_password(password) if password else None, int(time.time())),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,updated_at) VALUES(?,0,0,0,?,?,?)",
            (mid, settings.max_orders, settings.voting_power_base, int(time.time())),
        )
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _put_password(tok: str, payload: dict):
    return client.put("/api/me/password", headers=_auth(tok), json=payload)


# ─── set path (no stored hash) ───────────────────────────────────────────────

def test_google_only_member_sets_password_then_logs_in_with_it():
    email = "set-pw@sclub.test"
    _, tok = _mk_member(email, None)
    me = client.get("/api/me", headers=_auth(tok)).json()["member"]
    assert me["has_password"] is False and "password_hash" not in me
    r = _put_password(tok, {"new_password": "brand-new-pass-1"})   # no current_password
    assert r.status_code == 200 and r.json()["ok"] is True
    # email+password login now works — the whole point of setting one
    r = client.post("/api/auth/login", json={"email": email, "password": "brand-new-pass-1"})
    assert r.status_code == 200 and r.json()["member"]["email"] == email
    assert r.json()["member"]["has_password"] is True


def test_set_password_does_not_revoke_the_session_that_set_it():
    _, tok = _mk_member("set-pw-keep@sclub.test", None)
    assert _put_password(tok, {"new_password": "brand-new-pass-2"}).status_code == 200
    # no session_version bump on SET: the original (Google-minted) session lives
    assert client.get("/api/me", headers=_auth(tok)).status_code == 200


def test_set_path_still_rejects_short_new_password():
    _, tok = _mk_member("set-pw-short@sclub.test", None)
    assert _put_password(tok, {"new_password": "short"}).status_code == 422
    # nothing was stored
    r = client.post("/api/auth/login",
                    json={"email": "set-pw-short@sclub.test", "password": "shortpad1"})
    assert r.status_code == 401


def test_set_path_requires_an_interactive_session_not_an_api_key():
    """An API key must not be able to mint itself a full login (mirrors the
    require_session_member rationale on key management)."""
    mid, tok = _mk_member("set-pw-apikey@sclub.test", None)
    key = client.post("/api/me/api-keys", headers=_auth(tok),
                      json={"name": "t"}).json()["key"]
    r = client.put("/api/me/password", headers={"Authorization": f"Bearer {key}"},
                   json={"new_password": "sneaky-key-pass1"})
    assert r.status_code == 403
    with db() as conn:
        assert conn.execute("SELECT password_hash FROM members WHERE id=?",
                            (mid,)).fetchone()["password_hash"] is None


# ─── change path (stored hash) — unchanged strictness ────────────────────────

def test_change_still_403s_on_wrong_or_missing_current_password():
    _, tok = _mk_member("chg-pw@sclub.test", "original-pass-11")
    for payload in ({"current_password": "wrong-guess-111", "new_password": "whatever-pass-1"},
                    {"new_password": "whatever-pass-1"}):   # missing → defaults to ""
        r = _put_password(tok, payload)
        assert r.status_code == 403
        assert r.json()["detail"] == "current password is wrong"
    # and the real change flow still works + revokes the old session
    r = _put_password(tok, {"current_password": "original-pass-11",
                            "new_password": "rotated-pass-111"})
    assert r.status_code == 200
    assert client.get("/api/me", headers=_auth(tok)).status_code == 401   # bumped
