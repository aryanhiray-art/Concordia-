"""Member join / login / logout / me.

v1 is an invite-gated private club: `join` requires the shared INVITE_CODE and
creates the member; `login` requires the same INVITE_CODE + the member's email
(a shared club secret, not a per-user password). The session token is returned
in the body so the web app can store it and send it as a Bearer header; we also
set a cookie. Upgrade path: magic-link / OAuth per member.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from ..config import settings
from ..db import audit, db
from ..security import make_session
from .deps import COOKIE_NAME, current_member, load_member

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _EmailBody(BaseModel):
    @field_validator("email", check_fields=False)
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email")
        return v


class JoinBody(_EmailBody):
    invite_code: str
    email: str
    display_name: str


class LoginBody(_EmailBody):
    invite_code: str
    email: str


def _default_settings(member_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,updated_at) VALUES(?,?,?,?,?,?)",
            (member_id, 0, settings.default_buying_power_pct, settings.default_daily_cap_usd,
             settings.default_max_orders, int(time.time())),
        )


def _issue(resp: Response, member: dict) -> dict:
    token = make_session(member["id"], int(member.get("session_version", 1)))
    resp.set_cookie(COOKIE_NAME, token, max_age=30 * 24 * 3600, httponly=True,
                    secure=settings.app_env == "prod", samesite="lax")
    return {"token": token, "member": load_member(member["id"])}


@router.post("/join")
def join(body: JoinBody, resp: Response):
    if body.invite_code.strip() != settings.invite_code:
        raise HTTPException(status_code=403, detail="invalid invite code")
    email = body.email.lower()
    role = "owner" if email == settings.owner_email else "member"
    with db() as conn:
        existing = conn.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
        if existing:
            member = dict(existing)
        else:
            cur = conn.execute(
                "INSERT INTO members(email,display_name,role,invite_code_used,created_at) "
                "VALUES(?,?,?,?,?)",
                (email, body.display_name.strip(), role, body.invite_code.strip(), int(time.time())),
            )
            member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    audit(f"member:{member['id']}", "join", email, {"role": role})
    return _issue(resp, member)


@router.post("/login")
def login(body: LoginBody, resp: Response):
    if body.invite_code.strip() != settings.invite_code:
        raise HTTPException(status_code=403, detail="invalid invite code")
    with db() as conn:
        row = conn.execute("SELECT * FROM members WHERE email=?", (body.email.lower(),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="no member with that email — join first")
    return _issue(resp, dict(row))


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/whoami")
def whoami(request: Request):
    return {"member": current_member(request)}
