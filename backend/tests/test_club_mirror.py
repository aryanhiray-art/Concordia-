"""The demo shows the REAL club's record — via a file, never the live database.

The demo instance is a public sign-up with its own database. Its club-level
views had nothing to show, and seeding fixtures invented a track record that
never happened. So production publishes exactly two already-shared payloads to a
file (jobs/mirror_club) and the demo reads it (services/clubmirror).

What these tests pin:
  * the mirror is DEMO-ONLY — a real instance never serves it, so live members
    can't be shown a stale copy of their own club
  * the mirror job REFUSES to run on a demo instance (wrong direction)
  * it refuses to publish rows carrying member-scoped keys, so a future change
    to club_history cannot silently start leaking into a public demo
  * a missing/corrupt mirror falls back to local data instead of erroring

Run from backend/:  python -m pytest tests/test_club_mirror.py -q
"""
import json
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import clubmirror  # noqa: E402


def setup_module(_):
    init_db()


@pytest.fixture
def mirror_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    payload = {
        "mirrored_at": 1785300000,
        "history": [{"id": 1, "trade_date": "2026-07-28", "status": "executed",
                     "status_display": "executed", "basket": ["AAPL"], "voters": 3,
                     "human_voters": 3, "orders": {"placed": 24}}],
        "performance": {"start_date": "2026-07-01", "computed_at": 1785300000,
                        "n_sessions": 5, "points": [{"d": "2026-07-01", "club": 100.0, "spy": 100.0}],
                        "club_pct": 1.5, "spy_pct": 1.1},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    yield path
    os.unlink(path)


@pytest.fixture(autouse=True)
def _restore_settings():
    """settings is a frozen dataclass — the suite's house pattern for flipping a
    flag is object.__setattr__ (see test_email_verification), restored after."""
    before = (settings.demo_mode, settings.club_mirror_path)
    _set(before[0], before[1])
    yield
    _set(before[0], before[1])


def _set(demo: bool, path: str) -> None:
    object.__setattr__(settings, "demo_mode", demo)
    object.__setattr__(settings, "club_mirror_path", path)
    clubmirror._cache["at"], clubmirror._cache["payload"] = 0.0, None


def test_mirror_is_ignored_on_a_real_instance(mirror_file):
    """A live member must see their OWN club, never a mirrored copy."""
    _set(False, mirror_file)
    assert clubmirror.enabled() is False
    assert clubmirror.history(30) is None
    assert clubmirror.performance() is None


def test_demo_serves_the_mirrored_club(mirror_file):
    _set(True, mirror_file)
    rows = clubmirror.history(30)
    assert rows and rows[0]["trade_date"] == "2026-07-28"
    assert rows[0]["basket"] == ["AAPL"]
    perf = clubmirror.performance()
    assert perf["n_sessions"] == 5 and perf["club_pct"] == 1.5
    assert clubmirror.mirrored_at() == 1785300000


def test_missing_or_corrupt_mirror_falls_back(mirror_file):
    _set(True, "/nonexistent/club.json")
    assert clubmirror.history(30) is None          # → route uses local data

    with open(mirror_file, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    _set(True, mirror_file)
    assert clubmirror.history(30) is None


def test_mirror_job_refuses_to_run_on_a_demo_instance():
    from jobs import mirror_club
    _set(True, "")
    with pytest.raises(SystemExit):
        mirror_club.build()


def test_mirror_job_refuses_member_scoped_rows():
    """If club_history ever starts emitting identity, publishing must FAIL."""
    from jobs import mirror_club
    with pytest.raises(SystemExit):
        mirror_club._assert_clean([{"trade_date": "2026-07-28", "email": "a@b.c"}])
    with pytest.raises(SystemExit):
        mirror_club._assert_clean([{"trade_date": "2026-07-28", "member_id": 2}])
    mirror_club._assert_clean([{"trade_date": "2026-07-28", "basket": ["AAPL"], "voters": 3}])


def test_mirror_write_is_atomic_and_group_readable(tmp_path):
    from jobs import mirror_club
    out = str(tmp_path / "sub" / "club.json")
    mirror_club.write(out, {"mirrored_at": 1, "history": [], "performance": {}})
    assert json.load(open(out))["mirrored_at"] == 1
    assert oct(os.stat(out).st_mode & 0o777) == "0o640"
    assert not [f for f in os.listdir(os.path.dirname(out)) if f.endswith(".tmp")]
