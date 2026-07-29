"""The 8-char password floor is enforced at SIGNUP, not only on later changes.

It is enforced by `_EmailBody._valid_password` — a field_validator on the shared
base class with check_fields=False, so it applies to every body that carries a
`password` field (signup, login) WITHOUT a per-field min_length. That indirection
is easy to miss when skimming the models: SignupBody just says `password: str`,
which reads like no constraint at all. These tests pin the BEHAVIOUR so nobody
"tidies up" the base-class validator and silently drops the floor.

Each assertion checks the 422 actually names the password field — a bare
status-code check would pass on any unrelated validation error.

Run from backend/:  python -m pytest tests/test_signup_password_floor.py -q
"""
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("INVITE_CODE", "test-invite")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def setup_module(_):
    init_db()


def _signup(password: str, email: str, ip: str):
    """Signup with a distinct spoofed IP — the per-IP window is shared process-wide."""
    return client.post(
        "/api/auth/signup",
        json={
            "invite_code": settings.invite_code,
            "email": email,
            "password": password,
            "display_name": "Floor Test",
            "consent": True,
        },
        headers={"X-Forwarded-For": ip},
    )


def _rejected_field(resp) -> str | None:
    """Which field did validation actually reject? Guards against a vacuous pass."""
    detail = resp.json().get("detail")
    if not isinstance(detail, list) or not detail:
        return None
    return str(detail[0].get("loc", ["", ""])[-1])


def test_short_password_is_refused_at_signup():
    for pw in ("x", "short", "1234567"):          # 1, 5, 7 chars — all under the floor
        r = _signup(pw, f"floor-{len(pw)}@aclub.test", f"10.44.0.{len(pw)}")
        assert r.status_code == 422, f"{pw!r} ({len(pw)} chars) was accepted"
        # The rejection must be ABOUT the password, not some other field.
        assert _rejected_field(r) == "password", f"422 for {pw!r} was not about the password: {r.text}"

    with db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM members WHERE email LIKE 'floor-%@aclub.test'"
        ).fetchone()[0]
    assert n == 0, "a rejected signup still created a member row"


def test_eight_chars_is_accepted():
    r = _signup("12345678", "floor-ok@aclub.test", "10.44.1.1")
    assert r.status_code == 200, r.text
    assert r.json()["member"]["email"] == "floor-ok@aclub.test"


def test_login_shares_the_same_floor_via_the_base_class():
    """The validator lives on _EmailBody, so LoginBody inherits it too."""
    r = client.post("/api/auth/login", json={"email": "floor-ok@aclub.test", "password": "short"},
                    headers={"X-Forwarded-For": "10.44.2.1"})
    assert r.status_code == 422 and _rejected_field(r) == "password"
