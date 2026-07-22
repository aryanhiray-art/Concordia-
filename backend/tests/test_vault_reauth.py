"""Tests for vault.ensure_client marking needs-reauth at refresh time.

A dead refresh token (RHAuthError from oauth.refresh_bundle) must flip
rh_accounts.status to 'error' the moment the WEB path hits it — not only when
the executor's next trade cycle fetches a portfolio — so the member UI can
surface "reconnect Robinhood" immediately. Transient failures, and auth
failures where a concurrent refresher already persisted a fresh bundle, must
NOT mark: a blip or a lost rotation race never strands a member.

Run from backend/:  python -m pytest tests/test_vault_reauth.py -q
(uses a throwaway temp DB; RobinhoodClient construction is stubbed — no network).
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

from app.db import db, init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.robinhood.client import RHAuthError, RHTransientError  # noqa: E402


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _stale_bundle() -> dict:
    return {"access_token": "stale-tok", "refresh_token": "rt-old",
            "client_id": "cid", "expires_in": 3600, "obtained_at": 100}


def _fresh_bundle() -> dict:
    return {"access_token": "fresh-tok", "refresh_token": "rt-new",
            "client_id": "cid", "expires_in": 3600, "obtained_at": int(time.time())}


def _member(email: str) -> int:
    """Member row + a connected rh_accounts row holding a STALE encrypted bundle."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            (email, email.split("@")[0], "member", int(time.time())),
        )
        mid = cur.lastrowid
    vault.save_connection(mid, _stale_bundle(), f"ACC{mid}", True)
    return mid


def _rh_status(mid: int) -> str:
    with db() as conn:
        return conn.execute(
            "SELECT status FROM rh_accounts WHERE member_id=?", (mid,)
        ).fetchone()["status"]


def _reauth_audits(mid: int) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE action='rh_reauth_required' AND target=?",
            (str(mid),),
        ).fetchall()
    return [r["detail_json"] for r in rows]


class StubClient:
    """Stands in for RobinhoodClient (whose __init__ opens an MCP session)."""

    def __init__(self, access_token: str):
        self.access_token = access_token


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(vault, "RobinhoodClient", StubClient)


# ─── refresh-failure paths ───────────────────────────────────────────────────

def test_auth_error_marks_error_and_audits(monkeypatch):
    """Dead refresh token + no fresh bundle in the store → status='error',
    rh_reauth_required audited, and the RHAuthError still propagates."""
    mid = _member("reauth-dead@x.com")

    def _boom(bundle):
        raise RHAuthError("RH token refresh rejected 400: invalid_grant")

    monkeypatch.setattr(vault.oauth, "refresh_bundle", _boom)

    with pytest.raises(RHAuthError):
        vault.ensure_client(mid)

    assert _rh_status(mid) == "error"
    audits = _reauth_audits(mid)
    assert len(audits) == 1 and "invalid_grant" in audits[0]


def test_transient_error_does_not_mark(monkeypatch):
    """A network/5xx blip propagates but leaves status='connected' — the next
    caller retries with the same stored bundle."""
    mid = _member("reauth-blip@x.com")

    def _boom(bundle):
        raise RHTransientError("RH token refresh failed 503: upstream")

    monkeypatch.setattr(vault.oauth, "refresh_bundle", _boom)

    with pytest.raises(RHTransientError):
        vault.ensure_client(mid)

    assert _rh_status(mid) == "connected"
    assert _reauth_audits(mid) == []


def test_auth_error_adopts_concurrent_winner_without_marking(monkeypatch):
    """RHAuthError from a lost rotation race, but a CONCURRENT refresher already
    persisted a fresh bundle → adopt it (return a client), never mark."""
    mid = _member("reauth-race@x.com")
    calls = {"n": 0}

    def _load(member_id):
        # 1: top-of-function read, 2: double-check inside the lock (both stale,
        # so this caller becomes the refresher), 3: post-exception recheck (fresh)
        calls["n"] += 1
        return _stale_bundle() if calls["n"] <= 2 else _fresh_bundle()

    def _boom(bundle):
        raise RHAuthError("RH token refresh rejected 400: rotation reuse")

    monkeypatch.setattr(vault, "load_bundle", _load)
    monkeypatch.setattr(vault.oauth, "refresh_bundle", _boom)

    client = vault.ensure_client(mid)

    assert isinstance(client, StubClient)
    assert client.access_token == "fresh-tok"
    assert calls["n"] == 3
    assert _rh_status(mid) == "connected"
    assert _reauth_audits(mid) == []
