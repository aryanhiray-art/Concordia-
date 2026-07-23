"""Shared request dependencies: resolve the current member from a signed session.

Accepts the session token as either `Authorization: Bearer <token>` (the web
app stores it and sends it — robust across the Vercel↔API origin boundary) or a
`concord_session` cookie (handy for same-origin/admin use).
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..config import settings
from ..db import db
from ..security import ct_eq, read_session

COOKIE_NAME = "concord_session"


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def _api_key_from(request: Request) -> str | None:
    """A presented API key, from `X-API-Key` or an `Authorization: Bearer ck_...`."""
    k = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if k:
        return k.strip()
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        if tok.startswith("ck_"):
            return tok
    return None


def client_ip(request: Request) -> str:
    """Real client IP, honouring the proxy chain (nginx + Next.js rewrites)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # nginx APPENDS the real client to any client-supplied XFF header
        # ($proxy_add_x_forwarded_for), so the trustworthy address is the LAST
        # element — taking the first would let callers spoof rate-limit keys.
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


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
    # Never serialize secret columns into API payloads (the members row reaches
    # the owner dashboard + every load_member caller).
    for _secret in ("password_hash", "password", "totp_secret", "session_token"):
        out.pop(_secret, None)
    out["settings"] = dict(s) if s else None
    out["robinhood"] = dict(rh) if rh else None
    return out


def owner_member_id() -> int | None:
    """The real owner member row (role='owner', else matched by OWNER_EMAIL)."""
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM members WHERE role='owner' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row and settings.owner_email:
            row = conn.execute(
                "SELECT id FROM members WHERE lower(email)=? LIMIT 1", (settings.owner_email,)
            ).fetchone()
    return int(row["id"]) if row else None


def current_member(request: Request) -> dict | None:
    payload = read_session(_token_from(request))
    if payload:
        member = load_member(int(payload["mid"]))
        if not member:
            return None
        if int(payload.get("ver", 0)) != int(member.get("session_version", 1)):
            return None  # session revoked
        member["_auth"] = "session"
        return member
    # No valid session — try an API key (Bearer ck_... or X-API-Key header).
    ak = _api_key_from(request)
    if ak:
        from ..services import apikeys
        mid = apikeys.resolve_member_id(ak)
        if mid is not None:
            member = load_member(mid)
            if member:
                member["_auth"] = "apikey"
                return member
    return None


def require_member(request: Request) -> dict:
    member = current_member(request)
    if not member:
        raise HTTPException(status_code=401, detail="not authenticated")
    return member


def require_session_member(request: Request) -> dict:
    """Member authenticated by an interactive session (not an API key).

    Key management must not be reachable with an API key — otherwise a leaked key
    could mint more keys for itself.
    """
    member = require_member(request)
    if member.get("_auth") != "session":
        raise HTTPException(status_code=403, detail="log in to manage API keys")
    return member


def require_owner(request: Request) -> dict:
    # backend-to-backend: a matching static service token authenticates as owner
    if settings.admin_api_token and ct_eq(_token_from(request), settings.admin_api_token):
        return {"id": 0, "email": "service", "role": "owner", "display_name": "service"}
    member = current_member(request)
    if not member:
        raise HTTPException(status_code=401, detail="not authenticated")
    if member.get("role") != "owner":
        raise HTTPException(status_code=403, detail="owner only")
    return member
