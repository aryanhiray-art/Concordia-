"""Signing up must actually produce a member_settings row — on the LIVE schema shape.

The bug this pins (live, 2026-09-16 → 09-29): buying_power_pct was dropped from
the signup INSERT as a dead column. It IS dead — nothing reads it — but the
deployed table predates its DEFAULT, so there it is `REAL NOT NULL` with no
default. The INSERT became a NOT NULL violation, `OR IGNORE` swallowed it, and
signup returned 200 having created no settings row. Every later allocation write
is an UPDATE, so it matched zero rows and also reported success: the member could
not commit money and nothing anywhere said why. Two weeks and one real member.

Two things are tested, because either alone would have missed it:
  * against a FRESH schema (what db.py builds) signup writes the row, and
  * against the LIVE schema shape (that column stripped of its default) it
    STILL writes the row — which is the case CI never had.

Run from backend/:  python -m pytest tests/test_signup_settings_row.py -q
"""
import os
import sqlite3
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.routes import auth  # noqa: E402

_MEMBERS: list[int] = []


def setup_module(_):
    init_db()


def teardown_module(_):
    with db() as conn:
        for mid in _MEMBERS:
            conn.execute("DELETE FROM member_settings WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM members WHERE id=?", (mid,))


def _new_member(email: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,0)",
            (email, email.split("@")[0], "member"))
        mid = int(cur.lastrowid)
    _MEMBERS.append(mid)
    return mid


def _settings_count(mid: int) -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM member_settings WHERE member_id=?",
                            (mid,)).fetchone()[0]


def test_signup_creates_the_settings_row():
    """The row every other table joins against exists after signup."""
    mid = _new_member("newjoiner@x.com")

    auth._default_settings(mid)

    assert _settings_count(mid) == 1
    with db() as conn:
        row = dict(conn.execute("SELECT * FROM member_settings WHERE member_id=?",
                                (mid,)).fetchone())
    assert row["opted_in"] == 0, "a new member is opted OUT until they choose"
    assert row["max_orders"] == settings.max_orders


def test_default_settings_is_idempotent():
    """It runs on every signup path and must not fail a re-entry."""
    mid = _new_member("twice@x.com")

    auth._default_settings(mid)
    auth._default_settings(mid)

    assert _settings_count(mid) == 1


def test_default_settings_works_against_the_live_column_shape(tmp_path, monkeypatch):
    """The case CI did not have, run through the REAL function.

    The suite's own DB is built by db.py, where buying_power_pct carries
    `DEFAULT 0` — so omitting the column there works fine, which is exactly why
    the bug passed every test and still broke a real signup. The deployed table
    is OLDER than that default. So: build member_settings the way the live box
    actually has it, point `auth.db` at that, and call `_default_settings` for
    real. Hardcoding the column list in the test instead would prove nothing —
    it would pass whatever the shipping code does.
    """
    import contextlib

    path = tmp_path / "liveshape.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE member_settings (
        member_id        INTEGER PRIMARY KEY,
        opted_in         INTEGER NOT NULL DEFAULT 0,
        buying_power_pct REAL    NOT NULL,          -- no DEFAULT, exactly as deployed
        daily_cap_usd    REAL    NOT NULL,
        max_orders       INTEGER NOT NULL,
        voting_power     REAL    NOT NULL DEFAULT 0,
        updated_at       INTEGER NOT NULL)""")
    con.commit()

    @contextlib.contextmanager
    def _live_shape_db():
        yield con

    monkeypatch.setattr(auth, "db", _live_shape_db)

    auth._default_settings(4242)        # the real signup call

    assert con.execute("SELECT COUNT(*) FROM member_settings WHERE member_id=4242"
                       ).fetchone()[0] == 1, (
        "signup wrote no settings row on the live schema — this is the live bug: "
        "the member would get a 200 and then be unable to commit money")


def test_or_ignore_really_does_hide_a_not_null_failure(tmp_path):
    """Why the breakage was silent for two weeks rather than loud immediately —
    worth pinning so nobody 'simplifies' the verification away again."""
    con = sqlite3.connect(tmp_path / "trap.db")
    con.execute("""CREATE TABLE member_settings (
        member_id        INTEGER PRIMARY KEY,
        buying_power_pct REAL NOT NULL,
        updated_at       INTEGER NOT NULL)""")

    con.execute("INSERT OR IGNORE INTO member_settings(member_id,updated_at) VALUES(1,0)")

    assert con.execute("SELECT COUNT(*) FROM member_settings").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):      # the same statement, unmasked
        con.execute("INSERT INTO member_settings(member_id,updated_at) VALUES(2,0)")
    con.close()
