"""Shared request dependencies: resolve the current member from a signed session.

Accepts the session token as either `Authorization: Bearer <token>` (the web
app stores it and sends it — robust across the Vercel↔API origin boundary) or a
`concord_session` cookie (handy for same-origin/admin use).
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..config import settings
from ..db import db
from ..security import read_session

COOKIE_NAME = "concord_session"


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def load_member(member_id: int) -> dict | None:
    with db() as conn:
        m = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        if not m:
            return None
        s = conn.execute("SELECT * FROM member_settings WHERE member_id=?", (member_id,)).fetchone()
        rh = conn.execute(
            "SELECT account_number,agentic_allowed,status,connected_at FROM rh_accounts WHERE member_id=?",
            (member_id,),
        ).fetchone()
    out = dict(m)
    out["settings"] = dict(s) if s else None
    out["robinhood"] = dict(rh) if rh else None
    return out


def current_member(request: Request) -> dict | None:
    payload = read_session(_token_from(request))
    if not payload:
        return None
    member = load_member(int(payload["mid"]))
    if not member:
        return None
    if int(payload.get("ver", 0)) != int(member.get("session_version", 1)):
        return None  # session revoked
    return member


def require_member(request: Request) -> dict:
    member = current_member(request)
    if not member:
        raise HTTPException(status_code=401, detail="not authenticated")
    return member


def require_owner(request: Request) -> dict:
    # backend-to-backend: a matching static service token authenticates as owner
    if settings.admin_api_token and _token_from(request) == settings.admin_api_token:
        return {"id": 0, "email": "service", "role": "owner", "display_name": "service"}
    member = current_member(request)
    if not member:
        raise HTTPException(status_code=401, detail="not authenticated")
    if member.get("role") != "owner":
        raise HTTPException(status_code=403, detail="owner only")
    return member
