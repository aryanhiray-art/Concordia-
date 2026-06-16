"""Current member: profile + the single participation switch."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..db import audit, db
from ..services import power
from .deps import load_member, require_member

router = APIRouter(prefix="/api", tags=["member"])


class ParticipateBody(BaseModel):
    on: bool


@router.get("/me")
def me(member: dict = Depends(require_member)):
    return {"member": load_member(member["id"]), "power": power.member_power_view(member["id"])}


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
