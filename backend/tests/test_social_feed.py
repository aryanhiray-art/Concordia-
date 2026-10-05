"""Tests for the club feed — routes/social.py.

The contract under test, in the order the owner specified it:
  * ranking promotes AUTHOR VOTING POWER first, then net votes, then recency
  * up/down votes are one per member per post, changeable and withdrawable
  * foul language is rejected server-side (sanitizer + blocklist, including
    leetspeak and separator evasion) without Scunthorpe false positives
  * writes are session-only (a leaked API key reads but never speaks)
  * soft delete keeps thread shape; author or admin only
  * departed members' content disappears from listings

Run from backend/:  python -m pytest tests/test_social_feed.py -q
"""
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import apikeys  # noqa: E402
from app.services.textguard import profane, sanitize  # noqa: E402
from app.routes import social as _social  # noqa: E402

import pytest  # noqa: E402


class _GateOff:
    """The real settings with discussion_owner_only forced False.

    These tests cover the feed's MECHANICS (ranking, voting, moderation,
    departed-member cleanup), which are unchanged by who is allowed in. The
    owner-only gate added 2026-10-05 would otherwise 403 every one of them.

    Patched per-test rather than via DISCUSSION_OWNER_ONLY in the environment:
    app.config reads env once at import, and every test module sets its env with
    setdefault BEFORE importing the app — so whichever module imports config
    first wins and an env-based switch here would silently do nothing in a
    full-suite run. test_discussion_gate.py covers the gate itself.
    """

    def __init__(self, real):
        self._real = real

    discussion_owner_only = False

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def _open_the_floor(monkeypatch):
    monkeypatch.setattr(_social, "settings", _GateOff(_social.settings))

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.87.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, power: float = 0.0, role: str = "member") -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], role, hash_password("feed-pass-11"),
             now, 0, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO member_settings(member_id, voting_power, voting_power_share, "
            "updated_at) VALUES(?,?,?,?) ON CONFLICT(member_id) DO UPDATE SET "
            "voting_power=excluded.voting_power, voting_power_share=excluded.voting_power_share",
            (mid, power, power / 10000.0, now),
        )
    return mid, make_session(mid, 1)


def _post(tok: str, body: str, parent_id: int | None = None):
    return client.post("/api/social/posts", headers=_hdrs(tok),
                       json={"body": body, "parent_id": parent_id})


def _feed(tok: str, sort: str = "top"):
    r = client.get(f"/api/social/feed?sort={sort}", headers=_hdrs(tok))
    assert r.status_code == 200
    return r.json()["posts"]


# ─── textguard unit coverage ─────────────────────────────────────────────────

def test_sanitize_strips_control_and_collapses_whitespace():
    assert sanitize("  hey  there \n\n\n\n new   line  ") == "hey there\n\nnew line"
    assert sanitize("   ") is None
    assert len(sanitize("x" * 5000)) == 1000


def test_profanity_catches_plain_leet_and_separators():
    assert profane("well fuck")
    assert profane("this is sh1t")
    assert profane("f.u.c.k this basket")
    assert profane("fuckyou")            # no separator at all
    assert profane("what a b!tch move")


def test_profanity_scunthorpe_stays_clean():
    for ok in ("Scunthorpe United won", "a classy assessment", "the analyst was passionate",
               "Hancock raised guidance", "shiitake mushrooms"):
        assert not profane(ok), ok


# ─── auth boundary ───────────────────────────────────────────────────────────

def test_feed_requires_auth():
    assert client.get("/api/social/feed", headers=_hdrs()).status_code == 401


def test_api_key_reads_but_cannot_post_or_vote():
    mid, tok = _mk_member("keyed@example.com")
    key = apikeys.create_key(mid, "t")["key"]
    assert client.get("/api/social/feed", headers=_hdrs(key)).status_code == 200
    assert _post(key, "hello from a key").status_code == 403
    pid = _post(tok, "a real session post").json()["id"]
    r = client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(key), json={"dir": 1})
    assert r.status_code == 403


# ─── posting, sanitizing, filtering ─────────────────────────────────────────

def test_post_appears_sanitized_in_feed():
    mid, tok = _mk_member("poster@example.com")
    r = _post(tok, "  first   post  here  ")
    assert r.status_code == 200
    posts = _feed(tok, "new")
    mine = [p for p in posts if p["author"] and p["author"]["id"] == mid]
    assert mine and mine[0]["body"] == "first post here"


def test_profane_post_rejected_with_reason():
    _, tok = _mk_member("sailor@example.com")
    r = _post(tok, "this basket is sh1t")
    assert r.status_code == 422
    assert "clean" in r.json()["detail"]


def test_reply_nesting_is_flat_only():
    _, tok = _mk_member("nester@example.com")
    top = _post(tok, "top level").json()["id"]
    reply = _post(tok, "a reply", parent_id=top)
    assert reply.status_code == 200
    deeper = _post(tok, "too deep", parent_id=reply.json()["id"])
    assert deeper.status_code == 422


def test_post_rate_limit_burst():
    _, tok = _mk_member("flooder@example.com")
    codes = [_post(tok, f"post number {i}").status_code for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


# ─── votes ──────────────────────────────────────────────────────────────────

def test_vote_toggle_and_withdraw():
    _, tok_a = _mk_member("votera@example.com")
    _, tok_b = _mk_member("voterb@example.com")
    pid = _post(tok_a, "vote on me").json()["id"]

    r = client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_b), json={"dir": 1})
    assert r.json() == {"ok": True, "up": 1, "down": 0, "my_vote": 1}
    # change of heart: same member flips to down — one vote, not two
    r = client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_b), json={"dir": -1})
    assert r.json() == {"ok": True, "up": 0, "down": 1, "my_vote": -1}
    # withdraw entirely
    r = client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_b), json={"dir": 0})
    assert r.json() == {"ok": True, "up": 0, "down": 0, "my_vote": None}


# ─── ranking: power first, then votes, then recency ─────────────────────────

def test_top_sort_promotes_power_then_votes():
    _, tok_low = _mk_member("lowpower@example.com", power=10.0)
    _, tok_high = _mk_member("highpower@example.com", power=900.0)
    _, tok_v1 = _mk_member("v1@example.com")
    _, tok_v2 = _mk_member("v2@example.com")

    low_post = _post(tok_low, "low power, popular").json()["id"]
    high_post = _post(tok_high, "high power, no votes").json()["id"]
    # the low-power post gets two upvotes; the high-power post none
    for tok in (tok_v1, tok_v2):
        client.post(f"/api/social/posts/{low_post}/vote", headers=_hdrs(tok), json={"dir": 1})

    posts = _feed(tok_v1, "top")
    ids = [p["id"] for p in posts]
    # power outranks votes (the owner's rule) …
    assert ids.index(high_post) < ids.index(low_post)

    # … and among equal-power authors, net votes break the tie
    _, tok_low2 = _mk_member("lowpower2@example.com", power=10.0)
    unloved = _post(tok_low2, "equal power, no votes").json()["id"]
    posts = _feed(tok_v1, "top")
    ids = [p["id"] for p in posts]
    assert ids.index(low_post) < ids.index(unloved)


def test_new_sort_is_chronological():
    _, tok = _mk_member("chrono@example.com", power=500.0)
    a = _post(tok, "older post for chrono test").json()["id"]
    time.sleep(1.1)  # created_at has second resolution
    b = _post(tok, "newer post for chrono test").json()["id"]
    ids = [p["id"] for p in _feed(tok, "new")]
    assert ids.index(b) < ids.index(a)


# ─── delete + membership filter ─────────────────────────────────────────────

def test_soft_delete_keeps_thread_shape_and_admin_can_moderate():
    mid_a, tok_a = _mk_member("author@example.com")
    _, tok_b = _mk_member("bystander@example.com")
    _, tok_admin = _mk_member("mod@example.com", role="admin")

    pid = _post(tok_a, "post with a reply").json()["id"]
    _post(tok_b, "the reply survives", parent_id=pid)

    # a bystander cannot delete
    assert client.delete(f"/api/social/posts/{pid}", headers=_hdrs(tok_b)).status_code == 403
    # the author can; the row soft-deletes and the reply survives
    assert client.delete(f"/api/social/posts/{pid}", headers=_hdrs(tok_a)).status_code == 200
    posts = _feed(tok_b, "new")
    gone = [p for p in posts if p["id"] == pid]
    assert gone and gone[0]["deleted"] and gone[0]["body"] == "" and gone[0]["author"] is None
    assert gone[0]["replies"] and gone[0]["replies"][0]["body"] == "the reply survives"

    # admin moderates someone else's post; a CHILDLESS tombstone then
    # disappears from listings entirely (no reply shape to preserve)
    pid2 = _post(tok_b, "admin will remove this").json()["id"]
    assert client.delete(f"/api/social/posts/{pid2}", headers=_hdrs(tok_admin)).status_code == 200
    assert pid2 not in [p["id"] for p in _feed(tok_b, "new")]


def test_departed_members_posts_vanish_from_listings():
    mid, tok = _mk_member("leaver@example.com")
    _, tok_other = _mk_member("stayer@example.com")
    pid = _post(tok, "goodbye world").json()["id"]
    with db() as conn:
        conn.execute("UPDATE members SET left_at=? WHERE id=?", (int(time.time()), mid))
    ids = [p["id"] for p in _feed(tok_other, "new")]
    assert pid not in ids


# ─── the dial payload ───────────────────────────────────────────────────────

def test_power_stats_null_safe_and_populated():
    mid, tok = _mk_member("dialer@example.com", power=486.20)
    r = client.get("/api/social/me", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    # No snapshot row yet → EVERY money figure is null (the em-dash contract):
    # member_settings defaults to 0, and a fabricated "$0 / 0.0%" about real
    # money is worse than an honest dash.
    assert body["rank"] is None and body["as_of_week"] is None
    assert body["power_usd"] is None and body["share"] is None
    assert body["capital_usd"] is None and body["accuracy"] is None

    with db() as conn:
        conn.execute(
            "INSERT INTO voting_power_history(member_id, week_start, power, equity, "
            "prev_power, delta, created_at, share, rank_at_snapshot, n_active, pool_usd) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "2026-09-06", 486.20, 500.0, 400.0, 86.2, int(time.time()),
             0.0486, 3, 24, 10000.0),
        )
    body = client.get("/api/social/me", headers=_hdrs(tok)).json()
    assert body["rank"] == 3 and body["as_of_week"] == "2026-09-06"
    assert body["power_usd"] == 486.20 and abs(body["share"] - 0.04862) < 1e-9
    # headcount + pool stay OFF member surfaces (matching the ballot's posture)
    assert "n_active" not in body and "pool_usd" not in body


def test_deleted_high_power_post_does_not_stay_pinned():
    _, tok_big = _mk_member("bigshot@example.com", power=5000.0)
    _, tok_small = _mk_member("smallfry@example.com", power=5.0)
    big = _post(tok_big, "about to be removed").json()["id"]
    _post(tok_small, "a reply keeps the tombstone listed", parent_id=big)
    small = _post(tok_small, "modest but alive").json()["id"]
    client.delete(f"/api/social/posts/{big}", headers=_hdrs(tok_big))
    ids = [p["id"] for p in _feed(tok_small, "top")]
    # the tombstone survives (it anchors a live reply) but ranks BELOW live posts
    assert big in ids and small in ids
    assert ids.index(small) < ids.index(big)


def test_departed_voters_stop_counting():
    _, tok_a = _mk_member("author2@example.com")
    mid_v, tok_v = _mk_member("transient@example.com")
    pid = _post(tok_a, "count my votes honestly").json()["id"]
    client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_v), json={"dir": 1})
    assert [p for p in _feed(tok_a, "new") if p["id"] == pid][0]["up"] == 1
    with db() as conn:
        conn.execute("UPDATE members SET left_at=? WHERE id=?", (int(time.time()), mid_v))
    assert [p for p in _feed(tok_a, "new") if p["id"] == pid][0]["up"] == 0


def test_withdraw_vote_from_tombstone_allowed_new_votes_blocked():
    _, tok_a = _mk_member("author3@example.com")
    _, tok_v = _mk_member("regretter@example.com")
    pid = _post(tok_a, "vote then delete").json()["id"]
    _post(tok_a, "anchor reply", parent_id=pid)
    client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_v), json={"dir": 1})
    client.delete(f"/api/social/posts/{pid}", headers=_hdrs(tok_a))
    # withdrawing a standing vote from the tombstone works…
    r = client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_v), json={"dir": 0})
    assert r.status_code == 200 and r.json()["up"] == 0
    # …but a NEW vote on it is refused
    assert client.post(f"/api/social/posts/{pid}/vote", headers=_hdrs(tok_v),
                       json={"dir": 1}).status_code == 404


def test_removed_members_api_key_stops_authenticating():
    mid, _tok = _mk_member("keyleaver@example.com")
    key = apikeys.create_key(mid, "leaver")["key"]
    assert client.get("/api/social/feed", headers=_hdrs(key)).status_code == 200
    with db() as conn:
        conn.execute("UPDATE members SET left_at=? WHERE id=?", (int(time.time()), mid))
    assert client.get("/api/social/feed", headers=_hdrs(key)).status_code == 401


def test_profanity_unicode_and_vocabulary_fixes():
    assert profane("f\u00fcck this")                  # fück — diacritic fold
    for ok in ("a conspicuous rally", "auspicious start", "spicy earnings",
               "flame retardant supplier", "Dick's Sporting Goods beat"):
        assert not profane(ok), ok
    assert profane("what a retarded take")            # word-boundary still caught
