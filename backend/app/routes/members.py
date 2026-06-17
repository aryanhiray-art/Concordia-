"""Current member: profile + the single participation switch."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from fastapi import HTTPException
from pydantic import Field

from ..db import audit, db
from ..security import hash_password, verify_password
from ..services import alloc, positions, power
from .deps import load_member, require_member

router = APIRouter(prefix="/api", tags=["member"])


class ParticipateBody(BaseModel):
    on: bool


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AllocationBody(BaseModel):
    type: str = Field(pattern="^(percent|dollar)$")
    value: float = Field(ge=0)


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
    with db() as conn:
        conn.execute("UPDATE members SET password_hash=? WHERE id=?",
                     (hash_password(body.new_password), member["id"]))
    audit(f"member:{member['id']}", "change_password", None, None)
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
