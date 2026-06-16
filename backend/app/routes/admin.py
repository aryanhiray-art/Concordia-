"""Owner-only: overview, master ARM switch, kill, manual triggers, member drilldown."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import audit, db, is_armed, kv_get, kv_set, set_armed
from ..robinhood import vault
from ..services import executor, power
from ..services import voting as svc
from .deps import load_member, require_owner

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ArmBody(BaseModel):
    armed: bool


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/overview")
def overview(owner: dict = Depends(require_owner)):
    with db() as conn:
        members = conn.execute(
            "SELECT m.id,m.email,m.display_name,m.role,m.last_login_at,m.created_at,"
            "s.opted_in,s.voting_power,a.account_number,a.agentic_allowed,a.status AS rh_status,"
            "(SELECT delta FROM voting_power_history h WHERE h.member_id=m.id "
            "  ORDER BY h.week_start DESC LIMIT 1) AS power_delta "
            "FROM members m LEFT JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id ORDER BY s.voting_power DESC, m.id"
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
    total_power = sum(float(m["voting_power"] or 0) for m in members)
    return {
        "armed": is_armed(),
        "halt": kv_get("halt") == "1",
        "execution_mode": settings.execution_mode,
        "top_n": settings.top_n,
        "total_voting_power": round(total_power, 2),
        "schedule": {"open": settings.vote_open_at, "close": settings.vote_close_at,
                     "execute": settings.execute_at, "weekly_power": settings.weekly_power_at},
        "members": [dict(m) for m in members],
        "session": sess_view,
        "executions": execs,
    }


@router.get("/member/{member_id}")
def member_detail(member_id: int, owner: dict = Depends(require_owner)):
    """Everything about one member: live holdings, what they bought, power history."""
    base = load_member(member_id)
    if not base:
        raise HTTPException(404, "no such member")
    out: dict = {"member": base, "power": power.member_power_view(member_id)}

    session = svc.active_session()
    if session:
        out["session_date"] = session["trade_date"]
        out["picks"] = svc.member_picks(session["id"], member_id)
        with db() as conn:
            rows = conn.execute(
                "SELECT symbol,side,dollar_amount,status,error,created_at FROM executions "
                "WHERE session_id=? AND member_id=? ORDER BY symbol", (session["id"], member_id)
            ).fetchall()
        out["executions"] = [dict(r) for r in rows]

    # live Robinhood holdings (best-effort)
    acct = vault.account_number_for(member_id)
    rh = base.get("robinhood") or {}
    if acct and rh.get("status") == "connected":
        try:
            client = vault.ensure_client(member_id)
            port = (client.get_portfolio(acct).get("data") or {})
            bp = port.get("buying_power") or {}
            out["portfolio"] = {
                "total_value": _num(port.get("total_value")),
                "equity_value": _num(port.get("equity_value")),
                "cash": _num(port.get("cash")),
                "buying_power": _num(bp.get("buying_power") if isinstance(bp, dict) else bp),
            }
            pos = (client.get_positions(acct).get("data") or {}).get("positions") or []
            longs = [p for p in pos if p.get("type") == "long" and _num(p.get("quantity"))]
            syms = [p["symbol"] for p in longs]
            quotes = {}
            for i in range(0, len(syms), 20):
                res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
                for e in res:
                    q = e.get("quote") or {}
                    if q.get("symbol"):
                        quotes[q["symbol"]] = _num(q.get("last_trade_price"))
            holdings = []
            for p in longs:
                qty = _num(p.get("quantity")) or 0
                avg = _num(p.get("average_buy_price")) or 0
                last = quotes.get(p["symbol"])
                mv = (last or avg) * qty
                holdings.append({
                    "symbol": p["symbol"], "quantity": round(qty, 4), "avg_buy_price": avg,
                    "last": last, "market_value": round(mv, 2),
                    "pnl": round((last - avg) * qty, 2) if last is not None else None,
                })
            holdings.sort(key=lambda h: h["market_value"], reverse=True)
            out["holdings"] = holdings
        except Exception as e:  # noqa: BLE001
            out["rh_error"] = f"{type(e).__name__}: {e}"
    return out


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
    """Manually trigger a phase. `execute` honours dry_run unless live+armed."""
    if phase == "open":
        return {"session": svc.ensure_active_session()}
    if phase == "power":
        return {"snapshots": power.snapshot_all()}
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
    raise HTTPException(400, "phase must be open|close|execute|power")
