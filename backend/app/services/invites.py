"""Minted invite codes — per-person/per-batch admission, next to the legacy env code.

The club started with ONE shared INVITE_CODE env string: rotating it after a
leak means editing .env and restarting, and attribution is meaningless when
everyone typed the same word. These are DB rows instead: minted from the admin
console, individually expiring/limited/revocable, and recorded per member.

Two entry points, matching the signup flow's two phases:
- `check(code)`   — non-consuming validity test (the send-a-code step; nothing
                    is admitted yet, so nothing is counted).
- `redeem(code)`  — atomic consume at the moment an account is actually
                    created. BEGIN IMMEDIATE + re-check inside the transaction:
                    two concurrent signups racing a max_uses=1 code cannot both
                    get in.

Both accept the legacy env code too, so existing links keep working.
"""
from __future__ import annotations

import secrets
import time

from ..config import settings
from ..db import db
from ..security import ct_eq

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/O/1/l/i — codes get read aloud


def mint(created_by: int | None, note: str = "", expires_days: int | None = None,
         max_uses: int | None = None) -> dict:
    """Create one invite. Returns the full row (including the code)."""
    code = "cncd-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
    now = int(time.time())
    created_by = created_by or None   # static admin token authenticates as id 0 — not a member row
    expires_at = now + int(expires_days) * 86_400 if expires_days else None
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO invites(code,note,created_by,created_at,expires_at,max_uses) "
            "VALUES(?,?,?,?,?,?)",
            (code, (note or "").strip()[:120] or None, created_by, now, expires_at, max_uses),
        )
        row = conn.execute("SELECT * FROM invites WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _row_admits(row, now: int) -> bool:
    if row is None or row["revoked_at"]:
        return False
    if row["expires_at"] and now > int(row["expires_at"]):
        return False
    if row["max_uses"] is not None and int(row["uses"]) >= int(row["max_uses"]):
        return False
    return True


def check(code: str) -> bool:
    """Would this code admit right now? Never consumes a use."""
    code = (code or "").strip()
    if not code:
        return False
    if ct_eq(code, settings.invite_code):
        return True
    with db() as conn:
        row = conn.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
    return _row_admits(row, int(time.time()))


def redeem(code: str) -> str | None:
    """Consume one use. Returns the attribution string to store on the member
    (the code itself), or None if the code does not admit."""
    code = (code or "").strip()
    if not code:
        return None
    if ct_eq(code, settings.invite_code):
        return code                              # legacy shared code: nothing to count
    now = int(time.time())
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
            if not _row_admits(row, now):
                conn.execute("ROLLBACK")
                return None
            conn.execute("UPDATE invites SET uses = uses + 1 WHERE id=?", (row["id"],))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return code


def listing() -> list[dict]:
    """All minted invites, newest first, with a computed `active` flag."""
    now = int(time.time())
    with db() as conn:
        rows = conn.execute("SELECT * FROM invites ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = _row_admits(r, now)
        out.append(d)
    return out


def revoke(invite_id: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE invites SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (int(time.time()), invite_id),
        )
    return cur.rowcount > 0
