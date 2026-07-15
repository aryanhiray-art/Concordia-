"""Current member: profile + the single participation switch."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from fastapi import HTTPException
from pydantic import Field

from ..db import audit, db
from ..security import hash_password, verify_password
from ..services import alloc, apikeys, positions, power
from .deps import load_member, require_member, require_session_member

router = APIRouter(prefix="/api", tags=["member"])


class ParticipateBody(BaseModel):
    on: bool


class ApiKeyBody(BaseModel):
    name: str = Field(default="API key", max_length=60)


class PasswordBody(BaseModel):
    current_password: str
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


@router.get("/me/account")
def me_account(member: dict = Depends(require_member)):
    """Agentic-account balance + allocation + club-only P&L (live Robinhood reads)."""
    mid = member["id"]
    return {
        "allocation": alloc.member_allocation(mid),   # {type, value, resolved_usd, balance}
        "pnl": alloc.club_pnl(mid),
        "power": power.member_power_view(mid),
    }


@router.get("/me/trade-readiness")
def me_trade_readiness(force: bool = False, member: dict = Depends(require_member)):
    """Is the member's RH account cleared to place buys (questionnaire done)?"""
    return alloc.trade_readiness(member["id"], force=force)


@router.get("/me/holdings")
def me_holdings(member: dict = Depends(require_member)):
    """The member's live long positions, valued at the current quote."""
    return {"holdings": positions.holdings_with_value(member["id"])}


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
    return me_account(member)


@router.put("/me/password")
def change_password(body: PasswordBody, member: dict = Depends(require_member)):
    """Change your own password (e.g. after the owner reset it to a temp one)."""
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM members WHERE id=?", (member["id"],)).fetchone()
    if not row or not row["password_hash"] or not verify_password(body.current_password, row["password_hash"]):
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
    """The one on/off switch. Persists immediately; OFF disables voting."""
    with db() as conn:
        conn.execute(
            "UPDATE member_settings SET opted_in=?, updated_at=? WHERE member_id=?",
            (1 if body.on else 0, int(time.time()), member["id"]),
        )
    audit(f"member:{member['id']}", "participate", None, {"on": body.on})
    return {"member": load_member(member["id"])}
