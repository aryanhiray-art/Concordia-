"""Tests for POST /api/me/logout-all (log out everywhere).

Contract: session-only (api-key 403). Bumps members.session_version, so every
previously-minted session token — the calling one included — 401s immediately
(deps.current_member matches the token's `ver` against the row). Clears the
httpOnly cookie and audits. A fresh login works and mints a live session again.

Run from backend/:  python -m pytest tests/test_logout_all.py -q
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

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import apikeys  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()


def _hdrs(token: str) -> dict:
    """Spoofed per-call client IP (client_ip trusts the LAST XFF hop) + auth —
    keeps these tests off the shared per-IP windows of other modules."""
    _ip_counter[0] += 1
    return {"X-Forwarded-For": f"10.79.0.{_ip_counter[0]}",
            "Authorization": f"Bearer {token}"}


def _mk_member(email: str) -> tuple[int, str]:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, "LA Tester", "member", hash_password("hunter2hunter2"),
             int(time.time()), int(time.time())),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)


def test_logout_all_kills_every_existing_session():
    mid, tok = _mk_member("everywhere@laclub.test")
    other = make_session(mid, 1)   # a second device
    assert client.get("/api/me", headers=_hdrs(tok)).status_code == 200
    assert client.get("/api/me", headers=_hdrs(other)).status_code == 200

    r = client.post("/api/me/logout-all", headers=_hdrs(tok))
    assert r.status_code == 200 and r.json() == {"ok": True}
    # the httpOnly cookie is cleared in the same response
    assert "concord_session" in (r.headers.get("set-cookie") or "")

    # EVERY old token is dead — the calling one and the other device's alike
    assert client.get("/api/me", headers=_hdrs(tok)).status_code == 401
    assert client.get("/api/me", headers=_hdrs(other)).status_code == 401
    with db() as conn:
        assert conn.execute("SELECT session_version FROM members WHERE id=?",
                            (mid,)).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='logout_all' "
                            "AND actor=?", (f"member:{mid}",)).fetchone()[0] == 1

    # a fresh login mints a session at the NEW version, which works
    r = client.post("/api/auth/login", headers={"X-Forwarded-For": "10.79.1.1"},
                    json={"email": "everywhere@laclub.test", "password": "hunter2hunter2"})
    assert r.status_code == 200
    assert client.get("/api/me",
                      headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_api_key_auth_is_403_and_bumps_nothing():
    mid, tok = _mk_member("keyed@laclub.test")
    key = apikeys.create_key(mid, "t")["key"]
    r = client.post("/api/me/logout-all", headers=_hdrs(key))
    assert r.status_code == 403
    # the real session survived — the key couldn't lock the member out
    assert client.get("/api/me", headers=_hdrs(tok)).status_code == 200
    with db() as conn:
        assert conn.execute("SELECT session_version FROM members WHERE id=?",
                            (mid,)).fetchone()[0] == 1
