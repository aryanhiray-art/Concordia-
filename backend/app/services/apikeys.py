"""Personal API keys.

A member (or the owner, from forge) can mint a key that lets an external program
vote on their behalf — e.g. the research-voter. We store only the sha256 hash;
the raw `ck_live_...` key is returned once at creation. Auth resolves a presented
key back to a member id by prefix lookup + constant-time hash compare.
"""
from __future__ import annotations

import time

from ..db import audit, db
from ..security import ct_eq, hash_api_key, new_api_key


def create_key(member_id: int, name: str = "API key") -> dict:
    name = (name or "API key").strip()[:60] or "API key"
    full, prefix, kh = new_api_key()
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys(member_id,name,prefix,key_hash,created_at) VALUES(?,?,?,?,?)",
            (member_id, name, prefix, kh, now),
        )
        kid = cur.lastrowid
    audit(f"member:{member_id}", "api_key_create", str(kid), {"name": name, "prefix": prefix})
    # `key` is returned exactly once — never persisted in the clear.
    return {"id": kid, "name": name, "prefix": prefix, "key": full, "created_at": now}


def list_keys(member_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,prefix,created_at,last_used_at,revoked_at FROM api_keys "
            "WHERE member_id=? ORDER BY created_at DESC", (member_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_key(member_id: int, key_id: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND member_id=? AND revoked_at IS NULL",
            (int(time.time()), key_id, member_id),
        )
        changed = cur.rowcount
    if changed:
        audit(f"member:{member_id}", "api_key_revoke", str(key_id), None)
    return changed > 0


def resolve_member_id(presented: str | None) -> int | None:
    """Return the member id for a valid, non-revoked key, else None. Touches last_used_at."""
    if not presented or not presented.startswith("ck_"):
        return None
    prefix, kh = presented[:16], hash_api_key(presented)
    with db() as conn:
        rows = conn.execute(
            "SELECT id,member_id,key_hash FROM api_keys WHERE prefix=? AND revoked_at IS NULL",
            (prefix,),
        ).fetchall()
    for r in rows:
        if ct_eq(r["key_hash"], kh):
            with db() as conn:
                conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (int(time.time()), r["id"]))
            return int(r["member_id"])
    return None
