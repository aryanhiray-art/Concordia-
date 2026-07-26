"""Short-lived email verification codes (signup / password reset).

6 random digits, 15-minute expiry, sha256-only at rest, ONE active code per
(email, purpose) — issuing a new one replaces the old row, so only the newest
email's code works. Verification is constant-time and hard-capped at 5
attempts: the row is deleted on a hit AND on attempt exhaustion, so even the
correct code is rejected once 5 misses have burnt it (re-request required).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from ..db import db

TTL_SECONDS = 15 * 60
MAX_ATTEMPTS = 5


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def mint() -> str:
    """A fresh plaintext code — NOT yet persisted. Callers store() only after
    the email actually went out, so a failed send never clobbers the member's
    previous still-valid code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def store(email: str, purpose: str, code: str) -> None:
    """Persist `code` for (email, purpose), replacing any old one."""
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM email_codes WHERE email=? AND purpose=?", (email, purpose))
        conn.execute(
            "INSERT INTO email_codes(email,purpose,code_hash,expires_at,attempts,created_at) "
            "VALUES(?,?,?,?,0,?)",
            (email, purpose, _hash(code), now + TTL_SECONDS, now),
        )


def verify(email: str, purpose: str, code: str) -> bool:
    """True iff `code` matches the live code for (email, purpose). Consumes on hit."""
    now = int(time.time())
    with db() as conn:
        # opportunistic cleanup: expired codes (any email) never linger
        conn.execute("DELETE FROM email_codes WHERE expires_at < ?", (now,))
        row = conn.execute(
            "SELECT * FROM email_codes WHERE email=? AND purpose=?", (email, purpose)
        ).fetchone()
        if not row:
            return False
        if hmac.compare_digest(_hash(code or ""), row["code_hash"]):
            conn.execute("DELETE FROM email_codes WHERE id=?", (row["id"],))
            return True
        # miss: burn an attempt; the 5th miss deletes the row outright, so a
        # 6th try fails even with the correct code (brute-force hard stop).
        if int(row["attempts"]) + 1 >= MAX_ATTEMPTS:
            conn.execute("DELETE FROM email_codes WHERE id=?", (row["id"],))
        else:
            conn.execute("UPDATE email_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        return False
