"""Member signup / login / logout.

Invite-gated club: signup needs the shared INVITE_CODE + email + a password the
member creates (or a verified Google sign-in via /google). Login needs email +
password every time, or the Google button. Passwords are stored as PBKDF2
hashes (never plaintext). Every signup/login is logged for the owner, and each
signup records acceptance of the club agreement in `consents`.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from .. import ratelimit
from ..config import settings
from ..db import audit, db
from ..security import ct_eq, hash_password, make_session, verify_password
from ..services import google_auth
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
    consent: bool | None = None   # club-agreement acceptance; REQUIRED true in prod


class LoginBody(_EmailBody):
    email: str
    password: str


class GoogleBody(BaseModel):
    credential: str                  # Google ID token (JWT) from the sign-in button
    invite_code: str | None = None   # required only when the email is new to the club
    consent: bool | None = None      # ditto — new members must accept the agreement


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


def _clean_display_name(raw: str) -> str:
    """Collapse whitespace + strip control chars from a member-supplied name and cap
    length. A name flows into operator-facing reports and (via the verifier snapshot)
    an LLM prompt, so an embedded newline/control char could inject instructions or
    corrupt the report. Data hygiene, not just cosmetics."""
    s = "".join(ch for ch in (raw or "") if ch == " " or (ch.isprintable() and ch not in "\r\n\t"))
    s = " ".join(s.split())
    return s[:40] or "Member"


CONSENT_KIND = "club_agreement_v2"   # v2 2026-07-24: self-service deletion + explicit RH access scope
PRIVACY_KIND = "privacy_v1"          # the signup checkbox covers the privacy note too


def _record_consent(member_id: int) -> None:
    """Append the member's acceptances (both signup paths). The single signup
    checkbox covers BOTH documents, so both are recorded with timestamps."""
    now = int(time.time())
    with db() as conn:
        for kind in (CONSENT_KIND, PRIVACY_KIND):
            conn.execute("INSERT INTO consents(member_id,kind,version,at) VALUES(?,?,?,?)",
                         (member_id, kind, "1", now))


def _link_identity(member_id: int, info: dict) -> None:
    """Idempotently attach a verified Google identity (keyed by the stable `sub`)."""
    with db() as conn:
        if conn.execute("SELECT 1 FROM oauth_identities WHERE provider='google' AND subject=?",
                        (info["sub"],)).fetchone():
            return
        conn.execute(
            "INSERT INTO oauth_identities(member_id,provider,subject,email,created_at) "
            "VALUES(?,?,?,?,?)",
            (member_id, "google", info["sub"], info["email"], int(time.time())),
        )
    audit(f"member:{member_id}", "google_link", info["email"], {"sub": info["sub"]})


@router.post("/signup")
def signup(body: SignupBody, request: Request, resp: Response):
    ip = client_ip(request)
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not ct_eq(body.invite_code.strip(), settings.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invalid invite code")
    # Prod requires an explicit club-agreement acceptance (dev keeps old clients
    # working); whenever it IS given, it's recorded below either way.
    if settings.is_prod and body.consent is not True:
        raise HTTPException(status_code=403, detail="you must accept the club agreement")
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
            (email, _clean_display_name(body.display_name), role, "redacted", pw, int(time.time())),
        )
        member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    if body.consent is True:
        _record_consent(member["id"])
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


@router.post("/google")
def google(body: GoogleBody, request: Request, resp: Response):
    """Sign in with Google. Existing member (matched by verified email) → login;
    new email → invite-gated signup that must accept the club agreement."""
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="google sign-in not configured")
    ip = client_ip(request)
    if ratelimit.too_many(f"google:{ip}", 20, 900):
        raise HTTPException(status_code=429, detail="too many attempts — try again in a few minutes")
    try:
        info = google_auth.verify_id_token(body.credential)
    except ValueError as e:
        ratelimit.record(f"google:{ip}")
        raise HTTPException(status_code=401, detail=str(e))
    email = (info.get("email") or "").strip().lower()
    # Belt-and-braces: the verifier already enforces both, but a member row must
    # NEVER be minted/matched from an unverified or empty Google email.
    if not email or info.get("email_verified") not in (True, "true"):
        raise HTTPException(status_code=401, detail="google account email is not verified")

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE lower(email)=? AND COALESCE(is_bot,0)=0", (email,)
        ).fetchone()
    if row:
        # Existing account: Google just proved control of the email — log in
        # (invite/consent not re-asked) and remember the identity link.
        member = dict(row)
        with db() as conn:
            prior = conn.execute(
                "SELECT 1 FROM oauth_identities WHERE member_id=? AND provider='google'",
                (member["id"],),
            ).fetchone()
        if prior is None and member.get("password_hash"):
            # FIRST Google link onto a password account: Google just proved the
            # email's real owner is here, so whoever set that password (possibly
            # an invite-holding squatter who pre-registered this address) loses
            # access — password sign-in is disabled and every existing session
            # revoked. The rightful owner continues via Google alone.
            with db() as conn:
                conn.execute(
                    "UPDATE members SET password_hash=NULL, session_version=session_version+1 "
                    "WHERE id=?", (member["id"],),
                )
            member["session_version"] = int(member.get("session_version", 1)) + 1
            member["password_hash"] = None
            audit(f"member:{member['id']}", "google_link_password_disabled", email, None)
        _link_identity(member["id"], info)
        _log_event(member["id"], "login", request)
        return _issue(resp, member)

    # New member: same invite gate + per-IP signup window as password signup.
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not body.invite_code or not ct_eq(body.invite_code.strip(), settings.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invite code required or invalid")
    if body.consent is not True:
        raise HTTPException(status_code=403, detail="you must accept the club agreement")
    role = "owner" if ct_eq(email, settings.owner_email) else "member"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,invite_code_used,password_hash,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (email, _clean_display_name(info.get("name") or email.split("@")[0]),
             role, "redacted", None, int(time.time())),
        )
        member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    _record_consent(member["id"])
    _link_identity(member["id"], info)
    _log_event(member["id"], "signup", request)
    audit(f"member:{member['id']}", "signup", email, {"role": role, "via": "google"})
    ratelimit.record(f"signup:{ip}")   # successful signups count against the window too
    return _issue(resp, member)


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/whoami")
def whoami(request: Request):
    return {"member": current_member(request)}
