"""Member signup / login / logout.

Invite-gated club: signup needs the shared INVITE_CODE + email + a password the
member creates (or a verified Google sign-in via /google). Login needs email +
password every time, or the Google button. Passwords are stored as PBKDF2
hashes (never plaintext). Every signup/login is logged for the owner, and each
signup records acceptance of the club agreement in `consents`.
"""
from __future__ import annotations

import threading
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from .. import ratelimit
from ..config import settings
from ..db import audit, db
from ..security import ct_eq, hash_password, make_session, verify_password
from ..services import invites
from ..services import email_codes, google_auth, mailer
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
    email_code: str | None = None  # 6-digit code from /signup/start (required only when email_enabled)


class SignupStartBody(_EmailBody):
    invite_code: str
    email: str


class LoginBody(_EmailBody):
    email: str
    password: str


class ForgotBody(_EmailBody):
    email: str


class ResetBody(_EmailBody):
    email: str
    code: str
    new_password: str = Field(min_length=8)   # same floor as PasswordBody


class GoogleBody(BaseModel):
    credential: str                  # Google ID token (JWT) from the sign-in button
    invite_code: str | None = None   # required only when the email is new to the club
    consent: bool | None = None      # ditto — new members must accept the agreement


def _default_settings(member_id: int) -> None:
    """Give a brand-new member the settings row every other table assumes exists.

    buying_power_pct is dead — nothing reads it — but it MUST still be written
    here. The deployed table predates the column's DEFAULT: on the live DB it is
    `REAL NOT NULL` with no default (CREATE TABLE IF NOT EXISTS never alters an
    existing table, so db.py's `DEFAULT 0` only applies to a fresh database).
    Dropping it from this column list on 2026-09-16 turned the statement into a
    NOT NULL violation that `OR IGNORE` then SWALLOWED — signup returned 200 and
    created no settings row, so the next member to join could not commit money at
    all: every allocation write is an UPDATE, which matched zero rows and still
    reported success. Retiring the column for real means rebuilding the live
    table; until then it gets written.

    The row is verified after the fact because OR IGNORE cannot distinguish "it
    was already there" from "it could not be inserted" — and a member without
    settings is a half-created account, which must fail loudly rather than look
    like a successful signup.
    """
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,updated_at) VALUES(?,?,?,?,?,?,?)",
            (member_id, 0, 0, 0, settings.max_orders, settings.voting_power_base,
             int(time.time())),
        )
        ok = conn.execute("SELECT 1 FROM member_settings WHERE member_id=?",
                          (member_id,)).fetchone()
    if not ok:
        audit("system", "settings_insert_failed", str(member_id), None)
        raise HTTPException(
            status_code=500,
            detail="could not finish creating your account — nothing was charged or "
                   "committed; please tell the club owner before trying again")


def _log_event(member_id: int, kind: str, request: Request) -> None:
    ip = request.client.host if request.client else None
    with db() as conn:
        conn.execute("INSERT INTO login_events(member_id,ts,ip,kind) VALUES(?,?,?,?)",
                     (member_id, int(time.time()), ip, kind))
        conn.execute("UPDATE members SET last_login_at=? WHERE id=?", (int(time.time()), member_id))


def _issue(resp: Response, member: dict) -> dict:
    token = make_session(member["id"], int(member.get("session_version", 1)))
    resp.set_cookie(COOKIE_NAME, token, max_age=30 * 24 * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="lax")
    return {"token": token, "member": load_member(member["id"])}


def _clean_display_name(raw: str) -> str:
    """Collapse whitespace + strip control chars from a member-supplied name and cap
    length. A name flows into operator-facing reports and (via the verifier snapshot)
    an LLM prompt, so an embedded newline/control char could inject instructions or
    corrupt the report. Data hygiene, not just cosmetics."""
    s = "".join(ch for ch in (raw or "") if ch == " " or (ch.isprintable() and ch not in "\r\n\t"))
    s = " ".join(s.split())
    return s[:40] or "Member"


# Bumped whenever the DOCUMENT changes, not on a schedule: a `kind` has to mean
# exactly one text, or a stored acceptance stops being evidence of what was
# agreed to. v3 2026-08-28: weekly cadence throughout, the operator described as
# one individual rather than a company, and the governing-law clause removed
# (there is no entity to attach one to). privacy_v2 the same day: login-IP
# retention and the three processors.
#
# Members who accepted v2 stay recorded against v2 — which is still true of them.
# `consents` is written at signup only and nothing re-asks, so re-acceptance is a
# conversation the operator has directly, not something this constant triggers.
# v4 2026-09-04: cadence-durable §3 (mechanism + "see the app's schedule",
# currently daily) after the club's third cadence change outran v3's weekly text
# within a week. Members who accepted earlier versions stay recorded against
# them, per the v3 precedent: the owner tells them; nothing re-prompts.
CONSENT_KIND = "club_agreement_v4"
PRIVACY_KIND = "privacy_v2"          # the signup checkbox covers the privacy note too


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


def _send_code_email(email: str, purpose: str) -> str | None:
    """Mail a fresh (email, purpose) code. Returns the plaintext code on
    success, None on mail failure — callers must NEVER put the code in a
    response outside demo mode (the public demo has no inbox to check, so
    there the UI shows it on screen; see signup_start).

    The code is persisted only AFTER the send succeeds — a mail hiccup must not
    destroy the previous still-valid code the member may be about to type."""
    code = email_codes.mint()
    text = (f"Your Concordia code is: {code}\n\n"
            "It expires in 15 minutes.\n"
            "If you didn't request this, you can ignore this email.")
    html = ("<p>Your Concordia code is:</p>"
            f"<p style=\"font-size:24px;font-weight:700;letter-spacing:4px\">{code}</p>"
            "<p>It expires in 15 minutes.</p>"
            "<p>If you didn't request this, you can ignore this email.</p>")
    if not mailer.send(email, "Your Concordia code", html, text):
        return None
    email_codes.store(email, purpose, code)
    return code


@router.post("/signup/start")
def signup_start(body: SignupStartBody, request: Request):
    """Step 1 of signup when email verification is on: invite gate → email a code.

    Deliberately says NOTHING about whether the address already has an account
    (the code is sent regardless) — the existing-account collision surfaces only
    at final /signup, exactly as today. With email disabled this is a no-op
    handshake (code_required=false) so the web flow works in both modes.
    """
    ip = client_ip(request)
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not invites.check(body.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invalid invite code")
    if not settings.email_enabled:
        return {"code_required": False}
    # Two send windows: per-address (3 / 10 min) AND per-IP (12 / hour) — the
    # per-address cap alone would let one invite-holding IP spray codes at
    # unlimited DISTINCT inboxes (each address under its own fresh window),
    # making the club a spam cannon. The per-IP window closes that.
    if ratelimit.too_many(f"mailcode:{body.email}", 3, 600) or \
       ratelimit.too_many(f"mailip:{ip}", 12, 3600):
        raise HTTPException(status_code=429, detail="too many codes sent — try again in a few minutes")
    code = _send_code_email(body.email, "signup")
    if code is None:
        raise HTTPException(status_code=502,
                            detail="couldn't send the verification code — try again in a minute")
    ratelimit.record(f"mailcode:{body.email}")
    ratelimit.record(f"mailip:{ip}")
    # A demo account is still an account (owner decision 2026-07-28): the code
    # always travels by email — the response NEVER carries it, on any instance.
    # (Keyless local sandboxes print it to the terminal via mailer's demo path.)
    return {"code_required": True}


@router.post("/signup")
def signup(body: SignupBody, request: Request, resp: Response):
    ip = client_ip(request)
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not invites.check(body.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invalid invite code")
    # Prod requires an explicit club-agreement acceptance (dev keeps old clients
    # working); whenever it IS given, it's recorded below either way.
    if settings.is_prod and body.consent is not True:
        raise HTTPException(status_code=403, detail="you must accept the club agreement")
    email = body.email
    # Duplicate-account check BEFORE consuming anything (the email code OR an
    # invite use): a valid code must never be burnt just to learn 409
    # "account exists". Unconditional — the email-off path burnt minted invite
    # uses on duplicate signups when this lived under `if email_enabled`.
    with db() as conn:
        if conn.execute("SELECT 1 FROM members WHERE email=?", (email,)).fetchone():
            raise HTTPException(status_code=409, detail="account exists — log in instead")
    # Email verification (only when configured — otherwise byte-for-byte the old
    # flow). Checked after invite+consent so a burnt code never precedes those 403s.
    if settings.email_enabled:
        if not body.email_code:
            raise HTTPException(status_code=403, detail="enter the code we emailed you")
        if not email_codes.verify(email, "signup", body.email_code):
            raise HTTPException(status_code=403,
                                detail="that code didn't match or expired — use the newest email")
    role = "owner" if ct_eq(email, settings.owner_email) else "member"
    pw = hash_password(body.password)
    # Consume the invite LAST — after the email code verified and right before
    # the row exists — so failed signups never burn a limited-use code. The
    # legacy shared code stays "redacted" in the row (leak hygiene); minted
    # codes are recorded verbatim — attribution is their entire point.
    used = invites.redeem(body.invite_code)
    if used is None:
        raise HTTPException(status_code=403, detail="invalid invite code")
    attr = "redacted" if ct_eq(used, settings.invite_code) else used
    with db() as conn:
        if conn.execute("SELECT 1 FROM members WHERE email=?", (email,)).fetchone():
            # never overwrite an existing account (no passwordless-claim takeover)
            raise HTTPException(status_code=409, detail="account exists — log in instead")
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,invite_code_used,password_hash,"
            "email_verified_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, _clean_display_name(body.display_name), role, attr, pw,
             int(time.time()) if settings.email_enabled else None, int(time.time())),
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
        if prior is None and member.get("password_hash") and not member.get("email_verified_at"):
            # FIRST Google link onto a password account whose email was NEVER
            # verified: whoever set that password (possibly an invite-holding
            # squatter who pre-registered this address) never proved the inbox,
            # while Google just proved its real owner is here — so the untrusted
            # password is wiped and every existing session revoked. A VERIFIED
            # account keeps its password: the same person proved the inbox at
            # signup (or via reset), so this is just a link, not a takeover.
            with db() as conn:
                conn.execute(
                    "UPDATE members SET password_hash=NULL, session_version=session_version+1 "
                    "WHERE id=?", (member["id"],),
                )
            member["session_version"] = int(member.get("session_version", 1)) + 1
            member["password_hash"] = None
            audit(f"member:{member['id']}", "google_link_password_disabled", email, None)
        if not member.get("email_verified_at"):
            # Google verified the address — stamp it (AFTER the wipe decision
            # above, which keys off the pre-Google state).
            now = int(time.time())
            with db() as conn:
                conn.execute("UPDATE members SET email_verified_at=? WHERE id=? "
                             "AND email_verified_at IS NULL", (now, member["id"]))
            member["email_verified_at"] = now
        _link_identity(member["id"], info)
        _log_event(member["id"], "login", request)
        return _issue(resp, member)

    # New member: same invite gate + per-IP signup window as password signup.
    if ratelimit.too_many(f"signup:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many signups — try again later")
    if not body.invite_code or not invites.check(body.invite_code):
        ratelimit.record(f"signup:{ip}")
        raise HTTPException(status_code=403, detail="invite code required or invalid")
    if body.consent is not True:
        raise HTTPException(status_code=403, detail="you must accept the club agreement")
    used = invites.redeem(body.invite_code)
    if used is None:
        raise HTTPException(status_code=403, detail="invite code required or invalid")
    attr = "redacted" if ct_eq(used, settings.invite_code) else used
    role = "owner" if ct_eq(email, settings.owner_email) else "member"
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO members(email,display_name,role,invite_code_used,password_hash,"
                "email_verified_at,created_at) VALUES(?,?,?,?,?,?,?)",
                (email, _clean_display_name(info.get("name") or email.split("@")[0]),
                 role, attr, None, int(time.time()), int(time.time())),
            )
    except sqlite3.IntegrityError:
        # concurrent duplicate signup: refund the just-consumed invite use
        # (best-effort) and answer like the pre-check would have
        if attr != "redacted":
            with db() as conn:
                conn.execute("UPDATE invites SET uses = MAX(uses - 1, 0) WHERE code=?", (attr,))
        raise HTTPException(status_code=409, detail="account exists — log in instead")
    member = {"id": cur.lastrowid, "session_version": 1, "role": role}
    _default_settings(member["id"])
    _record_consent(member["id"])
    _link_identity(member["id"], info)
    _log_event(member["id"], "signup", request)
    audit(f"member:{member['id']}", "signup", email, {"role": role, "via": "google"})
    ratelimit.record(f"signup:{ip}")   # successful signups count against the window too
    return _issue(resp, member)


@router.post("/forgot")
def forgot(body: ForgotBody, request: Request):
    """Start a password reset. ALWAYS the same 200 shape — whether the account
    exists, is Google-only, or email is off entirely — so this endpoint can't be
    used to enumerate members. The code is issued+sent only when a matching
    non-bot member exists (completing /reset proves inbox control, so it works
    for verified and never-verified accounts alike)."""
    ip = client_ip(request)
    if ratelimit.too_many(f"forgot:{ip}", 10, 3600):
        raise HTTPException(status_code=429, detail="too many requests — try again later")
    if settings.email_enabled:
        if ratelimit.too_many(f"mailcode:{body.email}", 3, 600):
            raise HTTPException(status_code=429,
                                detail="too many codes sent — try again in a few minutes")
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM members WHERE lower(email)=? AND COALESCE(is_bot,0)=0",
                (body.email,),
            ).fetchone()
        if row:
            # Send from a background thread: the synchronous Resend round-trip
            # (~100ms+) only happening for real accounts was a timing oracle —
            # unknown emails returned near-instantly. Now both paths return at
            # thread-spawn speed. A send failure is audited (mail_error) but
            # NOT surfaced — a 502 only for real accounts would be the same
            # oracle in a different suit. The member just re-requests.
            threading.Thread(
                target=_send_code_email, args=(body.email, "reset"), daemon=True
            ).start()
        # burn the send window either way, so 429 timing can't enumerate either
        ratelimit.record(f"mailcode:{body.email}")
    ratelimit.record(f"forgot:{ip}")
    return {"ok": True, "email_enabled": settings.email_enabled}


@router.post("/reset")
def reset(body: ResetBody, request: Request):
    """Finish a password reset with the emailed code. One generic 403 for every
    failure (bad/expired code, unknown email, bot row) — no enumeration."""
    ip = client_ip(request)
    if ratelimit.too_many(f"reset:{ip}", 20, 900):
        raise HTTPException(status_code=429, detail="too many attempts — try again in a few minutes")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE lower(email)=? AND COALESCE(is_bot,0)=0",
            (body.email,),
        ).fetchone()
    # verify runs even without a member row (it just finds no code) so both
    # failure paths burn an attempt window and share one message + status.
    ok = email_codes.verify(body.email, "reset", body.code)
    if not ok or not row:
        ratelimit.record(f"reset:{ip}")
        raise HTTPException(status_code=403, detail="that code didn't match or expired")
    now = int(time.time())
    with db() as conn:
        # session_version bump: a password reset IS a credential change — every
        # existing session (possibly an attacker's) dies with the old password.
        # The code just proved inbox control, so the email is now verified too.
        conn.execute(
            "UPDATE members SET password_hash=?, session_version=session_version+1, "
            "email_verified_at=COALESCE(email_verified_at,?) WHERE id=?",
            (hash_password(body.new_password), now, row["id"]),
        )
    audit(f"member:{row['id']}", "reset_password", body.email, None)
    return {"ok": True}


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/whoami")
def whoami(request: Request):
    return {"member": current_member(request)}
