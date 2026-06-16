"""Member signup / login / logout.

Invite-gated club: signup needs the shared INVITE_CODE + email + a password the
member creates. Login needs email + password every time. Passwords are stored as
PBKDF2 hashes (never plaintext). Every signup/login is logged for the owner.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from ..config import settings
from ..db import audit, db
from ..security import hash_password, make_session, verify_password
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

    @field_validator("password", check_fields=False)
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v or "") < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class SignupBody(_EmailBody):
    invite_code: str
    email: str
    password: str
    display_name: str


class LoginBody(_EmailBody):
    email: str
    password: str


def _default_settings(member_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,updated_at) VALUES(?,?,?,?,?,?,?)",
            (member_id, 0, 0, 0, settings.max_orders, settings.voting_power_base, int(time.time())),
        )


def _log_event(member_id: int, kind: str, request: Request) -> None:
    ip = request.client.host if request.client else None
    with db() as conn:
        conn.execute("INSERT INTO login_events(member_id,ts,ip,kind) VALUES(?,?,?,?)",
                     (member_id, int(time.time()), ip, kind))
        conn.execute("UPDATE members SET last_login_at=? WHERE id=?", (int(time.time()), member_id))


def _issue(resp: Response, member: dict) -> dict:
    token = make_session(member["id"], int(member.get("session_version", 1)))
    resp.set_cookie(COOKIE_NAME, token, max_age=30 * 24 * 3600, httponly=True,
                    secure=settings.app_env == "prod", samesite="lax")
    return {"token": token, "member": load_member(member["id"])}


@router.post("/signup")
def signup(body: SignupBody, request: Request, resp: Response):
    if body.invite_code.strip() != settings.invite_code:
        raise HTTPException(status_code=403, detail="invalid invite code")
    email = body.email
    role = "owner" if email == settings.owner_email else "member"
    pw = hash_password(body.password)
    with db() as conn:
        existing = conn.execute("SELECT * FROM members WHERE email=?", (email,)).fetchone()
        if existing:
            existing = dict(existing)
            if existing.get("password_hash"):
                raise HTTPException(status_code=409, detail="account exists — log in instead")
            # claim a pre-existing passwordless account (e.g. the seeded owner)
            conn.execute("UPDATE members SET password_hash=?, display_name=? WHERE id=?",
                         (pw, body.display_name.strip(), existing["id"]))
            member = existing
        else:
            cur = conn.execute(
                "INSERT INTO members(email,display_name,role,invite_code_used,password_hash,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (email, body.display_name.strip(), role, body.invite_code.strip(), pw, int(time.time())),
            )
            member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    _log_event(member["id"], "signup", request)
    audit(f"member:{member['id']}", "signup", email, {"role": role})
    return _issue(resp, member)


@router.post("/login")
def login(body: LoginBody, request: Request, resp: Response):
    with db() as conn:
        row = conn.execute("SELECT * FROM members WHERE email=?", (body.email,)).fetchone()
    if not row or not verify_password(body.password, dict(row).get("password_hash")):
        raise HTTPException(status_code=401, detail="wrong email or password")
    _log_event(row["id"], "login", request)
    return _issue(resp, dict(row))


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/whoami")
def whoami(request: Request):
    return {"member": current_member(request)}
