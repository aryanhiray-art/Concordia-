"""The discussion floor is owner-only until launch — routes/social.py.

Hiding <ClubFeed/> in the web app is cosmetic; this is the half that actually
closes the feature. Without these, a member could still read the whole feed and
post into it with a bare fetch, and nothing would fail.

What is pinned here:
  * the SHIPPED default is closed — asserted in a clean subprocess, because this
    suite's other modules set config env vars and app.config reads env once at
    import, so an in-process read proves nothing about what deploys
  * every one of the four discussion routes is gated, not just the feed
  * /api/social/me stays OPEN — it is the voting-power dial's data, on every
    member's /club page, and gating it would blank the dial for the whole club
  * an owner still gets through
  * flipping the knob opens the floor (the launch path works)

Run from backend/:  python -m pytest tests/test_discussion_gate.py -q
"""
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routes import social as _social  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)
_ip = [0]


def setup_module(_):
    init_db()


def _hdrs(tok):
    _ip[0] += 1
    return {"Authorization": f"Bearer {tok}", "X-Forwarded-For": f"10.91.0.{_ip[0] % 250}"}


def _mk(email: str, role: str) -> str:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], role, hash_password("gate-pass-11"), now, 0, now))
        mid = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO member_settings(member_id) VALUES(?)", (mid,))
    return make_session(mid, 1)


class _Gate:
    def __init__(self, real, closed): self._real, self.discussion_owner_only = real, closed
    def __getattr__(self, n): return getattr(self._real, n)


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(_social, "settings", _Gate(_social.settings, True))


@pytest.fixture
def opened(monkeypatch):
    monkeypatch.setattr(_social, "settings", _Gate(_social.settings, False))


def test_the_shipped_default_is_closed():
    """Pin the default that DEPLOYS, not one this process happens to have.

    A plain `settings.discussion_owner_only` here would read whatever env the
    rest of the suite set first. A clean interpreter with no override is the
    only honest read of what ships.
    """
    env = {k: v for k, v in os.environ.items() if k != "DISCUSSION_OWNER_ONLY"}
    env["CONCORD_DB_PATH"] = tempfile.mkstemp(suffix=".db")[1]
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.'); from app.config import settings;"
         " print(settings.discussion_owner_only)"],
        capture_output=True, text=True, env=env, cwd=os.getcwd(), timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "True", f"discussion shipped OPEN: {out.stdout!r}"


def test_every_discussion_route_refuses_a_member(closed):
    tok = _mk("gate-member@test.local", "member")
    post = client.post("/api/social/posts", json={"body": "hello floor"}, headers=_hdrs(tok))
    assert post.status_code == 403, post.text
    for resp in (client.get("/api/social/feed", headers=_hdrs(tok)),
                 post,
                 client.post("/api/social/posts/1/vote", json={"dir": 1}, headers=_hdrs(tok)),
                 client.delete("/api/social/posts/1", headers=_hdrs(tok))):
        assert resp.status_code == 403, resp.text


def test_the_power_dial_still_loads_for_a_member(closed):
    """/api/social/me is the dial, not discussion — gating it blanks /club."""
    tok = _mk("gate-dial@test.local", "member")
    assert client.get("/api/social/me", headers=_hdrs(tok)).status_code == 200


def test_the_owner_still_gets_in(closed):
    tok = _mk("gate-owner@test.local", "owner")
    assert client.get("/api/social/feed", headers=_hdrs(tok)).status_code == 200
    assert client.post("/api/social/posts", json={"body": "owner can speak"},
                       headers=_hdrs(tok)).status_code == 200


def test_flipping_the_knob_opens_the_floor(opened):
    """The launch path: one knob, and members are back in."""
    tok = _mk("gate-open@test.local", "member")
    assert client.get("/api/social/feed", headers=_hdrs(tok)).status_code == 200
    assert client.post("/api/social/posts", json={"body": "floor is open"},
                       headers=_hdrs(tok)).status_code == 200
