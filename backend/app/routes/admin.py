"""Owner-only admin: overview, master ARM switch, kill, manual phase triggers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import audit, db, is_armed, kv_get, kv_set, set_armed
from ..services import executor
from ..services import voting as svc
from .deps import require_owner

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ArmBody(BaseModel):
    armed: bool


@router.get("/overview")
def overview(owner: dict = Depends(require_owner)):
    with db() as conn:
        members = conn.execute(
            "SELECT m.id,m.email,m.display_name,m.role,s.opted_in,s.buying_power_pct,"
            "s.daily_cap_usd,s.max_orders,a.account_number,a.agentic_allowed,a.status AS rh_status "
            "FROM members m LEFT JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id ORDER BY m.id"
        ).fetchall()
    session = svc.active_session()
    sess_view = svc.session_public(session, owner["id"]) if session else None
    execs = []
    if session:
        with db() as conn:
            rows = conn.execute(
                "SELECT e.*, m.display_name FROM executions e JOIN members m ON m.id=e.member_id "
                "WHERE e.session_id=? ORDER BY e.member_id,e.symbol", (session["id"],)
            ).fetchall()
        execs = [dict(r) for r in rows]
    return {
        "armed": is_armed(),
        "halt": kv_get("halt") == "1",
        "execution_mode": settings.execution_mode,
        "top_n": settings.top_n,
        "schedule": {"open": settings.vote_open_at, "close": settings.vote_close_at,
                     "execute": settings.execute_at},
        "members": [dict(m) for m in members],
        "session": sess_view,
        "executions": execs,
    }


@router.post("/arm")
def arm(body: ArmBody, owner: dict = Depends(require_owner)):
    set_armed(body.armed, actor=owner["email"])
    if body.armed:
        kv_set("halt", "0")
    return {"armed": is_armed()}


@router.post("/kill")
def kill(owner: dict = Depends(require_owner)):
    kv_set("halt", "1")
    set_armed(False, actor=owner["email"])
    audit(owner["email"], "kill", None, None)
    return {"armed": False, "halt": True}


@router.post("/run/{phase}")
def run_phase(phase: str, dry_run: bool = True, owner: dict = Depends(require_owner)):
    """Manually trigger a lifecycle phase. `execute` honours dry_run unless live+armed."""
    if phase == "open":
        return {"session": svc.ensure_active_session()}
    if phase == "close":
        session = svc.active_session()
        if not session:
            raise HTTPException(404, "no session")
        return {"session": svc.close_and_select(session["id"])}
    if phase == "execute":
        kv_set("halt", "0")
        session = svc.active_session()
        if not session:
            raise HTTPException(404, "no session")
        if session["status"] == "open":
            session = svc.close_and_select(session["id"])
        return executor.run(session, force_dry_run=dry_run)
    raise HTTPException(400, "phase must be open|close|execute")
