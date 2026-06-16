"""Current member: profile + settings."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..db import audit, db
from .deps import load_member, require_member

router = APIRouter(prefix="/api", tags=["member"])


class SettingsBody(BaseModel):
    opted_in: bool
    buying_power_pct: float = Field(ge=0, le=100)
    daily_cap_usd: float = Field(ge=0)
    max_orders: int | None = Field(default=None, ge=1, le=50)


@router.get("/me")
def me(member: dict = Depends(require_member)):
    return {"member": load_member(member["id"])}


@router.put("/me/settings")
def update_settings(body: SettingsBody, member: dict = Depends(require_member)):
    with db() as conn:
        row = conn.execute("SELECT max_orders FROM member_settings WHERE member_id=?",
                           (member["id"],)).fetchone()
        max_orders = body.max_orders if body.max_orders is not None else (row["max_orders"] if row else 10)
        conn.execute(
            "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
            "max_orders,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(member_id) DO UPDATE SET opted_in=excluded.opted_in,"
            "buying_power_pct=excluded.buying_power_pct,daily_cap_usd=excluded.daily_cap_usd,"
            "max_orders=excluded.max_orders,updated_at=excluded.updated_at",
            (member["id"], 1 if body.opted_in else 0, body.buying_power_pct, body.daily_cap_usd,
             max_orders, int(time.time())),
        )
    audit(f"member:{member['id']}", "settings", None, body.model_dump())
    return {"member": load_member(member["id"])}
