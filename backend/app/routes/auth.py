"""Member signup / login / logout.

Invite-gated club: signup needs the shared INVITE_CODE + email + a password the
member creates. Login needs email + password every time. Passwords are stored as
PBKDF2 hashes (never plaintext). Every signup/login is logged for the owner.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from .. import ratelimit
from ..config import settings
from ..db import audit, db
from ..security import ct_eq, hash_password, make_session, verify_password
from .deps import COOKIE_NAME, client_ip, current_member, load_member

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Run PBKDF2 even when the email is unknown, so login takes the same time either
# way (no user enumeration via timing).
_DUMMY_HASH = hash_password("concord-constant-time-placeholder")


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
    ip = client_ip(request)
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not ct_eq(body.invite_code.strip(), settings.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invalid invite code")
    email = body.email
    role = "owner" if ct_eq(email, settings.owner_email) else "member"
    pw = hash_password(body.password)
    with db() as conn:
        if conn.execute("SELECT 1 FROM members WHERE email=?", (email,)).fetchone():
            # never overwrite an existing account (no passwordless-claim takeover)
            raise HTTPException(status_code=409, detail="account exists — log in instead")
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,invite_code_used,password_hash,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, body.display_name.strip(), role, "redacted", pw, int(time.time())),
        )
        member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    _log_event(member["id"], "signup", request)
    audit(f"member:{member['id']}", "signup", email, {"role": role})
    # Count SUCCESSFUL signups against the per-IP window too (finding #24) — else
    # someone who knows the shared invite code could mass-create accounts (each a
    # floored voting-power sybil) without ever tripping the limit.
    ratelimit.record(f"signup:{ip}")
    return _issue(resp, member)


@router.post("/login")
def login(body: LoginBody, request: Request, resp: Response):
    ip = client_ip(request)
    # Key the account-lockout window by email+IP, not email alone (finding #22):
    # otherwise anyone who knows a victim's email can burn 8 attempts from any IP
    # and lock the victim out even with the correct password. Per-IP (ikey) still
    # caps an IP hammering many accounts.
    ekey, ikey = f"login:{body.email}:{ip}", f"loginip:{ip}"
    if ratelimit.too_many(ekey, 8, 900) or ratelimit.too_many(ikey, 40, 900):
        raise HTTPException(status_code=429, detail="too many attempts — try again in a few minutes")
    with db() as conn:
        row = conn.execute("SELECT * FROM members WHERE email=?", (body.email,)).fetchone()
    stored = dict(row).get("password_hash") if row else None
    ok = verify_password(body.password, stored or _DUMMY_HASH) and bool(stored)  # constant-ish time
    if not ok:
        ratelimit.record(ekey)
        ratelimit.record(ikey)
        raise HTTPException(status_code=401, detail="wrong email or password")
    ratelimit.clear(ekey)
    _log_event(row["id"], "login", request)
    return _issue(resp, dict(row))


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/whoami")
def whoami(request: Request):
    return {"member": current_member(request)}
