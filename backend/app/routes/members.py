"""Current member: profile + the single participation switch."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from fastapi import HTTPException
from pydantic import Field, field_validator

from .. import ratelimit
from ..config import settings
from ..db import audit, db, kv_get, kv_set
from ..robinhood import vault
from ..security import hash_password, verify_password
from ..services import accountsnap, alloc, apikeys, email_codes, equity_history, positions, power
from .deps import COOKIE_NAME, client_ip, load_member, require_member, require_session_member

router = APIRouter(prefix="/api", tags=["member"])

# At most one member-forced live refresh per this many seconds (POST
# /me/account/refresh) — the button must not become a way to hammer Robinhood.
FORCED_REFRESH_SECS = 30


class ParticipateBody(BaseModel):
    on: bool


class ApiKeyBody(BaseModel):
    name: str = Field(default="API key", max_length=60)


class PasswordBody(BaseModel):
    current_password: str = ""   # optional: Google-only accounts have none to prove
    new_password: str = Field(min_length=8)


class AllocationBody(BaseModel):
    type: str = Field(pattern="^(percent|dollar)$")
    # allow_inf_nan=False rejects Infinity/NaN — Pydantic v2 otherwise accepts
    # inf (inf >= 0), which is stored verbatim and then 500s the JSON encoder on
    # every read of the member's account (a self-inflicted DoS, finding #29).
    value: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)


@router.get("/me")
def me(member: dict = Depends(require_member)):
    return {"member": load_member(member["id"]), "power": power.member_power_view(member["id"])}


def _account_response(view: dict) -> dict:
    """The fixed /me/account contract, sliced out of an accountsnap view."""
    return {
        "allocation": view["allocation"],   # {type, value, resolved_usd, balance}
        "pnl": view["pnl"],
        "power": view["power"],
        "as_of": view["as_of"],             # epoch of the last successful broker read
        "stale": view["stale"],             # older than accountsnap.STALE_SECS
        "live": view["live"],               # this response did the broker read itself
    }


@router.get("/me/account")
def me_account(member: dict = Depends(require_member)):
    """Agentic-account balance + allocation + club-only P&L.

    Served from the member's cached snapshot (services/accountsnap) — a local DB
    read, not the ~4s of live Robinhood calls this used to cost on every visit.
    The snapshot is rebuilt by the 10-minute interval refresher, refreshed behind
    the response once it ages past FRESH_SECS, and built inline only on a
    member's very first load. `as_of`/`stale` tell the UI how old the numbers are.
    """
    return _account_response(accountsnap.get(member["id"]))


@router.post("/me/account/refresh")
def me_account_refresh(member: dict = Depends(require_member)):
    """Force a live re-read now (the UI's refresh button). Rate-limited so it can't
    be turned into a broker hammer."""
    mid = member["id"]
    key = f"acctrefresh:{mid}"
    if ratelimit.too_many(key, 1, FORCED_REFRESH_SECS):
        raise HTTPException(
            status_code=429,
            detail=f"your account was just refreshed — wait {FORCED_REFRESH_SECS}s between refreshes",
        )
    ratelimit.record(key)
    return _account_response(accountsnap.refresh_view(mid))


@router.get("/me/trade-readiness")
def me_trade_readiness(force: bool = False, member: dict = Depends(require_member)):
    """Is the member's RH account cleared to place buys (questionnaire done)?"""
    return alloc.trade_readiness(member["id"], force=force)


# ── Limited margin (settlement-delay killer) ──────────────────────────────
# A cash agentic account can only spend SETTLED funds: sale proceeds arrive
# T+1, so the club's redeploy for that member runs a day behind. Robinhood's
# "limited margin" removes exactly that delay — trade with unsettled funds,
# NO borrowing, NO leverage — via a self-serve switch flow the member
# completes on Robinhood. The wizard uses this endpoint to route cash members
# into that flow, and to make staying on cash an explicit, informed choice.

_MARGIN_TTL = 120  # seconds to cache the probe (get_accounts is a broker call)

_MARGIN_FLOW = "flow_id=brokerage.switch-to-margin-account&source=concordia&target_margin_type=limited"


def _margin_fallback_links(acct: str) -> dict:
    # Deterministic self-serve URLs (same params the broker's own tool emits) —
    # used only if the upgrade-info tool is unavailable for this token.
    q = f"account_number={acct}&account_type=individual&{_MARGIN_FLOW}"
    return {
        "web_upgrade_url": f"https://robinhood.com/contact?{q}",
        "mobile_upgrade_url": f"https://applink.robinhood.com/self_serve?{q}",
        "upgrade_fallback": "In the Robinhood app: Settings → Investing → Account type → switch the account to margin (limited).",
    }


@router.get("/me/margin-status")
def me_margin_status(force: bool = False, member: dict = Depends(require_member)):
    """The member's agentic-account type + the limited-margin upgrade path."""
    mid = member["id"]
    acct = vault.account_number_for(mid)
    if not acct:
        return {"connected": False}
    ck = f"margin_status:{mid}"
    if not force:
        cached = kv_get(ck)
        if cached:
            try:
                c = json.loads(cached)
                if time.time() - c.get("ts", 0) < _MARGIN_TTL:
                    return {k: v for k, v in c.items() if k != "ts"}
            except Exception:  # noqa: BLE001
                pass
    out: dict = {"connected": True, "account_type": None, "unsettled_funds": None,
                 "delayed": None, "upgrade": None}
    try:
        client = vault.ensure_client(mid)
        accounts = (client.get_accounts().get("data") or {}).get("accounts") or []
        row = next((a for a in accounts if a.get("account_number") == acct), None)
        if row:
            atype = row.get("type")
            out["account_type"] = atype
            out["unsettled_funds"] = row.get("unsettled_funds")
            # Only a plain cash account waits on settlement; margin and
            # limited_margin both redeploy sale proceeds immediately.
            out["delayed"] = atype == "cash"
            if atype == "cash":
                try:
                    info = (client.get_limited_margin_upgrade_info(acct).get("data") or {})
                    if info.get("eligible"):
                        out["upgrade"] = {
                            "web_upgrade_url": info.get("web_upgrade_url"),
                            "mobile_upgrade_url": info.get("mobile_upgrade_url"),
                            "upgrade_fallback": info.get("upgrade_fallback"),
                        }
                except Exception:  # noqa: BLE001
                    out["upgrade"] = _margin_fallback_links(acct)
                if out["upgrade"] is None:
                    out["upgrade"] = _margin_fallback_links(acct)
            # Last-known type, persisted for the executor/admin surfaces.
            kv_set(f"acct_type:{mid}", atype or "")
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:140]
    out["acknowledged"] = kv_get(f"margin_ack:{mid}") == "1"
    kv_set(ck, json.dumps({**out, "ts": int(time.time())}))
    return out


class MarginAckBody(BaseModel):
    acknowledged: bool


@router.put("/me/margin-ack")
def me_margin_ack(body: MarginAckBody, member: dict = Depends(require_member)):
    """Record that a cash-account member understands their trades are delayed
    (sale proceeds settle T+1 before the club can redeploy them)."""
    kv_set(f"margin_ack:{member['id']}", "1" if body.acknowledged else "0")
    audit(f"member:{member['id']}", "margin_ack", None,
          {"acknowledged": body.acknowledged})
    return {"ok": True, "acknowledged": body.acknowledged}


@router.get("/me/holdings")
def me_holdings(member: dict = Depends(require_member)):
    """The member's long positions valued at the quote, from the same cached
    snapshot as /me/account — so Overview and Account together cost zero broker
    calls instead of five.

    Each row carries `club`: whether the CLUB's ledger holds this symbol for
    the member. False = a personal position that happens to live in the same
    account (the owner's VMRK) — the UI labels those instead of implying the
    basket bought them. DB-only; costs no broker call."""
    view = accountsnap.get(member["id"])
    club_syms = {s for s, q in positions.club_shares(member["id"]).items() if q > 1e-6}
    holdings = [{**h, "club": h.get("symbol") in club_syms} for h in view["holdings"]]
    return {"holdings": holdings, "as_of": view["as_of"], "stale": view["stale"]}


@router.get("/me/equity")
def me_equity(range: str = equity_history.DEFAULT_RANGE,
              member: dict = Depends(require_member)):
    """Recorded account-value history → {range, points:[{t,v}], resolution,
    first_ts, ok}.

    Every point is a REAL observation of the member's total account value,
    recorded on each successful snapshot refresh (~10 min). Nothing is
    interpolated: long ranges are thinned by keeping the last real point per
    bucket (`resolution` says which), never by averaging. `first_ts` is the
    earliest point on record and `ok` is False when the range holds fewer than
    two points, so the UI can say "not enough history yet" instead of drawing a
    line through invented data.
    """
    try:
        return equity_history.series(member["id"], range)
    except equity_history.UnknownRange:
        raise HTTPException(status_code=400, detail="no such range")


@router.put("/me/allocation")
def set_allocation(body: AllocationBody, member: dict = Depends(require_member)):
    if body.type == "percent" and body.value > 100:
        raise HTTPException(status_code=422, detail="percent must be 0–100")
    # Vote-weight freeze (finding #9): you can't change your stake while a basket
    # you voted in is closed-but-not-yet-executed — that window is exactly when
    # your frozen vote weight is being turned into real orders on every account.
    from ..services import voting as _voting
    if _voting.member_in_closed_unexecuted_session(member["id"]):
        raise HTTPException(
            status_code=409,
            detail="allocation is locked while this cycle's basket is executing — try again once it completes",
        )
    with db() as conn:
        conn.execute(
            "UPDATE member_settings SET alloc_type=?, alloc_value=?, updated_at=strftime('%s','now') "
            "WHERE member_id=?", (body.type, body.value, member["id"]),
        )
    # re-snapshot voting power from the new allocation (best-effort; needs RH)
    try:
        power.snapshot_member(member["id"])
    except Exception:  # noqa: BLE001
        pass
    audit(f"member:{member['id']}", "allocation", None, {"type": body.type, "value": body.value})
    # Force a live rebuild rather than serving the cache: the member changed their
    # stake and must see the new resolved_usd (and the power just re-snapshotted
    # above) immediately, not whatever the last interval refresh captured.
    return _account_response(accountsnap.refresh_view(member["id"]))


class DisplayNameBody(BaseModel):
    display_name: str


@router.put("/me/display-name")
def set_display_name(body: DisplayNameBody, member: dict = Depends(require_member)):
    """Change your own display name.

    Session-only, same gate as the password paths: the name is how every other
    member sees this account, so a leaked API key must not be able to rewrite it.
    """
    if member.get("_auth") != "session":
        raise HTTPException(status_code=403, detail="log in to change your display name")
    name = (body.display_name or "").strip()
    if not name or len(name) > 40:
        raise HTTPException(status_code=422, detail="display name must be 1–40 characters")
    with db() as conn:
        conn.execute("UPDATE members SET display_name=? WHERE id=?", (name, member["id"]))
    audit(f"member:{member['id']}", "display_name", None, {"display_name": name})
    return {"member": load_member(member["id"])}


@router.put("/me/password")
def change_password(body: PasswordBody, member: dict = Depends(require_member)):
    """Change your own password (e.g. after the owner reset it to a temp one) —
    or SET a first one on a Google-only account (no stored hash)."""
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM members WHERE id=?", (member["id"],)).fetchone()
    if row and not row["password_hash"]:
        # Session-only (like key management): the change path below is gated by
        # knowing the current password, but here there is none — without this, a
        # leaked API key could mint itself a full interactive login.
        if member.get("_auth") != "session":
            raise HTTPException(status_code=403, detail="log in to set a password")
        # Google-only account setting its FIRST password: there is no current
        # password to prove, and no session_version bump — nothing pre-existing
        # to revoke, since every live session on a passwordless account was
        # minted by Google sign-in (which already proved email ownership);
        # bumping would only log the member out right after they set a password.
        # The squatter-protection revocation already happened at Google-link
        # time (auth.py google()).
        with db() as conn:
            conn.execute("UPDATE members SET password_hash=? WHERE id=?",
                         (hash_password(body.new_password), member["id"]))
        audit(f"member:{member['id']}", "set_password", None, None)
        return {"ok": True}
    if not row or not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status_code=403, detail="current password is wrong")
    # Bump session_version so ALL existing sessions/tokens are revoked on a
    # password change (finding #3) — otherwise a stolen token survives the reset
    # for its full 7-day TTL. Mirrors the owner reset-password path (admin.py).
    with db() as conn:
        conn.execute(
            "UPDATE members SET password_hash=?, session_version=session_version+1 WHERE id=?",
            (hash_password(body.new_password), member["id"]),
        )
    audit(f"member:{member['id']}", "change_password", None, None)
    return {"ok": True}


@router.post("/me/logout-all")
def logout_all(request: Request, resp: Response, member: dict = Depends(require_member)):
    """Log out everywhere: bump session_version so EVERY existing session/token
    (this one included) dies instantly — deps.current_member rejects any token
    whose `ver` no longer matches. Session-only, same rationale as the password
    paths: a leaked API key must not be able to lock the member out of their
    own browser sessions."""
    if member.get("_auth") != "session":
        raise HTTPException(status_code=403, detail="log in to log out everywhere")
    with db() as conn:
        conn.execute("UPDATE members SET session_version=session_version+1 WHERE id=?",
                     (member["id"],))
    # the httpOnly cookie in THIS browser is now a dead token — clear it too
    resp.delete_cookie(COOKIE_NAME)
    audit(f"member:{member['id']}", "logout_all", None, None)
    return {"ok": True}


class VerifyEmailBody(BaseModel):
    code: str


def _require_interactive(member: dict) -> None:
    """Session-only (like set-password): verification mints trust for the whole
    account, so a leaked API key must not be able to drive it."""
    if member.get("_auth") != "session":
        raise HTTPException(status_code=403, detail="log in to verify your email")


@router.post("/me/verify-email/start")
def verify_email_start(request: Request, member: dict = Depends(require_member)):
    """Email a verification code to the logged-in member's own address.

    For GRANDFATHERED accounts (created before email verification existed) that
    the UI gates post-login via needs_email_verification. Already-verified and
    email-disabled cases are cheap no-ops so the client can call unconditionally."""
    _require_interactive(member)
    if member.get("email_verified"):
        return {"sent": False, "already_verified": True}
    if not settings.email_enabled:
        return {"sent": False}
    ip = client_ip(request)
    email = member["email"]
    # Same two send windows as signup/start: per-address AND per-IP.
    if ratelimit.too_many(f"mailcode:{email}", 3, 600) or \
       ratelimit.too_many(f"mailip:{ip}", 12, 3600):
        raise HTTPException(status_code=429, detail="too many codes sent — try again in a few minutes")
    from .auth import _send_code_email
    if not _send_code_email(email, "verify"):
        raise HTTPException(status_code=502, detail="couldn't send the code — try again in a minute")
    ratelimit.record(f"mailcode:{email}")
    ratelimit.record(f"mailip:{ip}")
    return {"sent": True}


@router.post("/me/verify-email")
def verify_email(body: VerifyEmailBody, request: Request, member: dict = Depends(require_member)):
    """Finish verification with the emailed code → stamp email_verified_at."""
    _require_interactive(member)
    ip = client_ip(request)
    if ratelimit.too_many(f"verifyip:{ip}", 20, 900):
        raise HTTPException(status_code=429, detail="too many attempts — try again in a few minutes")
    if not email_codes.verify(member["email"], "verify", body.code):
        ratelimit.record(f"verifyip:{ip}")
        raise HTTPException(status_code=403,
                            detail="that code didn't match or expired — use the newest email")
    with db() as conn:
        # COALESCE: never move an existing stamp (e.g. Google verified mid-flow)
        conn.execute("UPDATE members SET email_verified_at=COALESCE(email_verified_at,?) WHERE id=?",
                     (int(time.time()), member["id"]))
    audit(f"member:{member['id']}", "email_verified", member["email"], None)
    return {"ok": True, "member": load_member(member["id"])}


class EmailChangeStartBody(BaseModel):
    new_email: str

    # same floor as auth._EmailBody — normalize, then a cheap sanity check
    @field_validator("new_email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email")
        return v


class EmailChangeConfirmBody(EmailChangeStartBody):
    code: str


@router.post("/me/email/start")
def email_change_start(body: EmailChangeStartBody, request: Request,
                       member: dict = Depends(require_member)):
    """Step 1 of changing the account email: mail a 6-digit code to the NEW
    address (proof the member controls that inbox — the change lands only via
    /me/email/confirm). Session-only like verify-email: the address is where
    password resets go, so a leaked API key must not be able to redirect it.
    The response NEVER carries the code (house rule, test_email_verification)."""
    _require_interactive(member)
    if not settings.email_enabled:
        # without mail there is no way to prove the new inbox — fail closed
        raise HTTPException(status_code=503,
                            detail="email isn't configured — ask the owner to change your address")
    new_email = body.new_email
    with db() as conn:
        # own row included on purpose: "change" to your current address is a no-op
        if conn.execute("SELECT 1 FROM members WHERE lower(email)=?", (new_email,)).fetchone():
            raise HTTPException(status_code=409, detail="that email already has an account")
    ip = client_ip(request)
    # Same two send windows as signup/start + verify-email/start.
    if ratelimit.too_many(f"mailcode:{new_email}", 3, 600) or \
       ratelimit.too_many(f"mailip:{ip}", 12, 3600):
        raise HTTPException(status_code=429, detail="too many codes sent — try again in a few minutes")
    from .auth import _send_code_email
    if not _send_code_email(new_email, "email_change"):
        raise HTTPException(status_code=502, detail="couldn't send the code — try again in a minute")
    ratelimit.record(f"mailcode:{new_email}")
    ratelimit.record(f"mailip:{ip}")
    return {"sent": True}


@router.post("/me/email/confirm")
def email_change_confirm(body: EmailChangeConfirmBody, request: Request,
                         member: dict = Depends(require_member)):
    """Finish the email change with the code that went to the new address."""
    _require_interactive(member)
    ip = client_ip(request)
    if ratelimit.too_many(f"emailchangeip:{ip}", 20, 900):
        raise HTTPException(status_code=429, detail="too many attempts — try again in a few minutes")
    new_email = body.new_email
    if not email_codes.verify(new_email, "email_change", body.code):
        ratelimit.record(f"emailchangeip:{ip}")
        raise HTTPException(status_code=403,
                            detail="that code didn't match or expired — use the newest email")
    old_email = member["email"]
    with db() as conn:
        # re-check INSIDE the write transaction: two members racing the same
        # address between start and confirm must not both land it
        if conn.execute("SELECT 1 FROM members WHERE lower(email)=? AND id!=?",
                        (new_email, member["id"])).fetchone():
            raise HTTPException(status_code=409, detail="that email already has an account")
        # the code just proved the new inbox, so the address arrives verified —
        # a member never loses email_verified by changing their email
        conn.execute("UPDATE members SET email=?, email_verified_at=? WHERE id=?",
                     (new_email, int(time.time()), member["id"]))
    audit(f"member:{member['id']}", "email_change", new_email, {"from": old_email})
    return {"member": load_member(member["id"])}


@router.get("/me/api-keys")
def list_api_keys(member: dict = Depends(require_session_member)):
    return {"keys": apikeys.list_keys(member["id"])}


@router.post("/me/api-keys")
def create_api_key(body: ApiKeyBody, member: dict = Depends(require_session_member)):
    """Mint a key — returned ONCE. Use it as `Authorization: Bearer ck_...`."""
    return apikeys.create_key(member["id"], body.name)


@router.delete("/me/api-keys/{key_id}")
def revoke_api_key(key_id: int, member: dict = Depends(require_session_member)):
    if not apikeys.revoke_key(member["id"], key_id):
        raise HTTPException(status_code=404, detail="no such key")
    return {"ok": True}


@router.put("/me/participate")
def participate(body: ParticipateBody, member: dict = Depends(require_member)):
    """The one on/off switch. Persists immediately; OFF disables voting.

    Same vote-weight freeze as /me/allocation and /me/compound. Without it,
    opting out is a free option on a real-money basket: vote at full weight,
    watch the tally, then opt out before execution and fund none of it. The
    executor selects `WHERE s.opted_in=1`, so the defection reads as a routine
    skip row — and accuracy is scored off the `votes` table, never `executions`,
    so the defector would still be credited for picks their money never bought.
    """
    from ..services import voting as _voting
    if _voting.member_has_live_ballot(member["id"]):
        raise HTTPException(
            status_code=409,
            detail="participation is locked while a ballot you voted in is still "
                   "running — it unlocks once that cycle executes",
        )
    with db() as conn:
        conn.execute(
            "UPDATE member_settings SET opted_in=?, updated_at=? WHERE member_id=?",
            (1 if body.on else 0, int(time.time()), member["id"]),
        )
    audit(f"member:{member['id']}", "participate", None, {"on": body.on})
    return {"member": load_member(member["id"])}


@router.put("/me/compound")
def set_compound(body: ParticipateBody, member: dict = Depends(require_member)):
    """Compounding stake on/off (default ON: the slice rides its own P&L in
    both directions; OFF pins deployment to the committed figure). Same freeze
    window as an allocation change — sizing must not flip mid-execution."""
    from ..services import voting as _voting
    if _voting.member_in_closed_unexecuted_session(member["id"]):
        raise HTTPException(
            status_code=409,
            detail="locked while this cycle's basket is executing — try again once it completes",
        )
    with db() as conn:
        conn.execute(
            "UPDATE member_settings SET compound=? WHERE member_id=?",
            (1 if body.on else 0, member["id"]),
        )
    audit(f"member:{member['id']}", "compound", None, {"on": body.on})
    return {"member": load_member(member["id"])}


@router.put("/me/email-reminders")
def email_reminders(body: ParticipateBody, member: dict = Depends(require_member)):
    """Ballot-lifecycle email opt-out. Deliberately does NOT touch updated_at:
    that timestamp doubles as the stake-freeze / demo buy-in clock, and a mail
    preference must never reset either."""
    with db() as conn:
        conn.execute(
            "UPDATE member_settings SET email_reminders=? WHERE member_id=?",
            (1 if body.on else 0, member["id"]),
        )
    audit(f"member:{member['id']}", "email_reminders", None, {"on": body.on})
    return {"member": load_member(member["id"])}


class DeleteAccountBody(BaseModel):
    confirm: str


@router.delete("/me")
def delete_account(body: DeleteAccountBody, member: dict = Depends(require_session_member)):
    """Self-service account deletion (terms §7): trading stops instantly, the
    Robinhood connection and every credential are destroyed, and personal
    identifiers are erased. The club's BOOKS survive anonymized — votes, trade
    and power history rows keep their member_id but that id now points at a
    scrubbed row (no name, no email, no way to log in). Consent records are
    retained as legal proof the agreement was accepted. Positions are untouched:
    they live in the member's own brokerage account, which the club never owned.
    Session-only auth (an API key must not be able to destroy the account) and
    a typed confirmation phrase. The owner can't self-delete — the club would
    be headless; transfer ownership first."""
    if (body.confirm or "").strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail='type DELETE to confirm')
    if member.get("role") == "owner":
        raise HTTPException(status_code=403,
                            detail="the owner account can't delete itself — transfer ownership first")
    mid = member["id"]
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # kill switch first: no future cycle may touch this member — and a
            # FULL settings reset, not just the switch: stake, caps and
            # reputation are personal state, and if the same person rejoins
            # later they must start from scratch, remembering nothing
            # accuracy_short/accuracy_long MUST be zeroed alongside `accuracy`.
            # `accuracy` is DERIVED from those two components on every scoring
            # run, so zeroing only the derived column left the components intact
            # and the next score_cycle recomputed the deleted member's
            # reputation straight back out of them.
            conn.execute("UPDATE member_settings SET opted_in=0, buying_power_pct=0, "
                         "daily_cap_usd=0, voting_power=0, voting_power_share=0, "
                         "last_capital_usd=0, alloc_type='percent', alloc_value=0, "
                         "accuracy=0, accuracy_short=0, accuracy_long=0, "
                         "cycles_participated=0, updated_at=strftime('%s','now') "
                         "WHERE member_id=?", (mid,))
            # revoke broker access + every way to authenticate
            conn.execute("DELETE FROM rh_accounts WHERE member_id=?", (mid,))
            # the display snapshot caches their balances/holdings — erase it too
            conn.execute("DELETE FROM account_snapshots WHERE member_id=?", (mid,))
            # ...and their recorded account-value history (personal financial
            # data, not part of the club's anonymized books)
            conn.execute("DELETE FROM equity_points WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM api_keys WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM oauth_identities WHERE member_id=?", (mid,))
            conn.execute("DELETE FROM login_events WHERE member_id=?", (mid,))
            # erase identity; bump session_version so every live session dies now
            # left_at is the membership primitive every service now filters on;
            # the anonymized email alone was an implicit convention that four
            # call sites (voting tally, power snapshot, cycle scoring, admin
            # overview) never checked, so a departed member could still weigh a
            # ballot that gets traded with other people's money.
            conn.execute("UPDATE members SET email=?, display_name='Deleted member', "
                         "password_hash=NULL, session_version=session_version+1, "
                         "left_at=COALESCE(left_at, CAST(strftime('%s','now') AS INTEGER)) "
                         "WHERE id=?",
                         (f"deleted-{mid}@deleted.invalid", mid))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    audit("system", "account_deleted", str(mid), None)
    return {"ok": True}
