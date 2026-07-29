"""Tests for PUT /api/me/display-name (member self-rename).

Contract:
- PUT /api/me/display-name {display_name} -> {member} with the new name stored
  (stripped of surrounding whitespace) and an audit row written.
- SESSION-ONLY, same gate as the password paths: an API key (bots, scripts)
  gets 403 — a leaked key must not be able to rewrite who a member appears as.
- Validation: stripped, 1..40 chars; empty/whitespace-only/41+ -> 422.
- 401 unauthenticated.

Run from backend/:  python -m pytest tests/test_display_name.py -q
(members inserted directly + per-test spoofed IPs, same rationale as
test_admin_panel.py.)
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module sets, so this module behaves
# the same alone as inside the full suite (one process-wide config singleton).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import apikeys  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.86.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("name-pass-11"),
             now, 0, now),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


# ─── the contract ────────────────────────────────────────────────────────────

def test_rename_happy_path_strips_stores_and_audits():
    mid, tok = _mk_member("rename-happy@nclub.test")
    r = client.put("/api/me/display-name", headers=_hdrs(tok),
                   json={"display_name": "  Rohan the Renamed  "})
    assert r.status_code == 200
    assert r.json()["member"]["display_name"] == "Rohan the Renamed"
    with db() as conn:
        row = conn.execute("SELECT display_name FROM members WHERE id=?", (mid,)).fetchone()
        aud = conn.execute(
            "SELECT 1 FROM audit_log WHERE actor=? AND action='display_name'",
            (f"member:{mid}",)).fetchone()
    assert row["display_name"] == "Rohan the Renamed"   # stripped, persisted
    assert aud is not None                              # the rename is audited

    # exactly 40 chars after stripping is the boundary — accepted
    forty = "x" * 40
    r40 = client.put("/api/me/display-name", headers=_hdrs(tok),
                     json={"display_name": f"  {forty}  "})
    assert r40.status_code == 200
    assert r40.json()["member"]["display_name"] == forty


def test_rename_is_session_only_api_key_gets_403():
    mid, tok = _mk_member("rename-key@nclub.test")
    key = apikeys.create_key(mid, "rename test")["key"]
    r = client.put("/api/me/display-name", headers=_hdrs(key),
                   json={"display_name": "Bot Rename"})
    assert r.status_code == 403
    assert "log in" in r.json()["detail"]
    with db() as conn:
        row = conn.execute("SELECT display_name FROM members WHERE id=?", (mid,)).fetchone()
    assert row["display_name"] == "rename-key"          # untouched


def test_rename_validation_422s():
    mid, tok = _mk_member("rename-len@nclub.test")
    for bad in ("", "   ", "x" * 41):
        r = client.put("/api/me/display-name", headers=_hdrs(tok),
                       json={"display_name": bad})
        assert r.status_code == 422, f"{bad!r} must 422"
    with db() as conn:
        row = conn.execute("SELECT display_name FROM members WHERE id=?", (mid,)).fetchone()
    assert row["display_name"] == "rename-len"          # nothing stored


def test_rename_401_unauthenticated():
    assert client.put("/api/me/display-name", headers=_hdrs(),
                      json={"display_name": "Nobody"}).status_code == 401
