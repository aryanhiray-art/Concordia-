"""Admin console (owner + cofounder): overview, panel, master ARM switch, kill,
manual triggers, member drilldown.

Most routes take require_admin — the cofounder ('admin' role) has equal rights.
The exceptions stay require_owner because they act AS the owner's member
identity: minting/revoking the owner's API keys and casting/seeding the owner's
research-voter ballot.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..bots import ollama
from ..calendar_us import cycle_phase, next_trading_day, today_et
from ..config import settings
from ..db import audit, db, is_armed, kv_get, kv_set, set_armed
from ..robinhood import vault
from ..services import adminview, alloc, apikeys, bots, executor, power, reconcile, research
from ..services import voting as svc
from .deps import load_member, owner_member_id, require_admin, require_owner

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ArmBody(BaseModel):
    armed: bool


class ApiKeyBody(BaseModel):
    name: str = "API key"


class ProgramVoteBody(BaseModel):
    symbols: list[str] | None = None
    dry_run: bool = False


def _owner_mid() -> int:
    mid = owner_member_id()
    if mid is None:
        raise HTTPException(404, "no owner member found")
    return mid


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/panel")
def admin_panel(admin: dict = Depends(require_admin)):
    """The admin console payload — FAST, DB-only, no live Robinhood calls.

    (member_allocation-style live balance reads run 1.6s+ per member; everything
    here comes from rows the jobs already maintain — see services/adminview.py,
    which the morning digest job shares so console and email never drift.)
    """
    return adminview.panel()


@router.get("/schedule")
def schedule(admin: dict = Depends(require_admin)):
    """What the cadence does today + the next 7 trading days, pre-resolved.

    The console shouldn't reimplement cycle_phase (weekday parity, holidays,
    the mwf both/top-up split) in TypeScript — it renders exactly what the
    executor's own calendar says. cycle_phase's None means "no trading today";
    the payload says "idle" so the UI never branches on null-vs-string.
    """
    today = today_et()
    upcoming = []
    d = today
    for _ in range(7):
        d = next_trading_day(d)
        upcoming.append({"date": d.isoformat(), "weekday": d.strftime("%a"),
                         "phase": cycle_phase(d) or "idle"})
    return {
        "cadence": settings.cycle_cadence,
        "today": {"date": today.isoformat(), "phase": cycle_phase(today) or "idle"},
        "upcoming": upcoming,
    }


# ─── run reports + audit/login browsers (DB-only, no broker calls) ───────────
# Freshness philosophy: like /panel, these endpoints read ONLY rows the runs
# and jobs already wrote — an admin browsing history must never trigger live
# Robinhood traffic.

_RUN_COLS = ('id, trade_date, phase, mode, "trigger", will_place, started_at, '
             "finished_at, placed, skipped, errors, verify_verdict")


def _run_row(r) -> dict:
    out = dict(r)
    out["will_place"] = bool(out.get("will_place"))
    return out


def _parse_json(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@router.get("/runs")
def runs_list(limit: int = 30, before: int | None = None,
              admin: dict = Depends(require_admin)):
    """Executor run history, newest first. `before=<id>` pages older rows.

    summary_json deliberately stays out of the list — a summary can be tens of
    KB per run; the drilldown endpoint below serves it one run at a time.
    """
    limit = max(1, min(limit, 200))
    q = f"SELECT {_RUN_COLS} FROM run_reports"  # noqa: S608 — column list is a constant
    params: list = []
    if before is not None:
        q += " WHERE id < ?"
        params.append(before)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = conn.execute(q, params).fetchall()
    return {"runs": [_run_row(r) for r in rows]}


@router.get("/runs/{run_id}")
def run_detail(run_id: int, admin: dict = Depends(require_admin)):
    """One run's list fields + the FULL parsed summary (per-member orders,
    buying power, notes, verification)."""
    with db() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS}, summary_json FROM run_reports WHERE id=?",  # noqa: S608
            (run_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "no such run")
    out = _run_row(row)
    out["summary"] = _parse_json(out.pop("summary_json", None))
    return out


@router.get("/audit")
def audit_browse(limit: int = 50, before: int | None = None,
                 action: str | None = None, actor: str | None = None,
                 admin: dict = Depends(require_admin)):
    """The audit trail, newest first, finally browsable: parsed detail on every
    row, `before=<id>` pagination, exact-match `action=` / `actor=` filters
    (optional, combinable)."""
    limit = max(1, min(limit, 500))
    conds, params = [], []
    if before is not None:
        conds.append("id < ?")
        params.append(before)
    if action:
        conds.append("action = ?")
        params.append(action)
    if actor:
        conds.append("actor = ?")
        params.append(actor)
    q = "SELECT id, ts, actor, action, target, detail_json FROM audit_log"
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = conn.execute(q, params).fetchall()
    return {"entries": [
        {"id": r["id"], "ts": r["ts"], "actor": r["actor"], "action": r["action"],
         "target": r["target"], "detail": _parse_json(r["detail_json"])}
        for r in rows
    ]}


@router.get("/logins")
def logins_list(limit: int = 30, admin: dict = Depends(require_admin)):
    """Recent sign-ins (login_events joined to members), newest first. Bots are
    excluded — they never log in; a bot row here would mean forged sessions."""
    limit = max(1, min(limit, 200))
    with db() as conn:
        rows = conn.execute(
            "SELECT l.ts, l.member_id, m.display_name, m.email, l.ip, l.kind "
            "FROM login_events l JOIN members m ON m.id = l.member_id "
            "WHERE COALESCE(m.is_bot, 0) = 0 ORDER BY l.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"logins": [dict(r) for r in rows]}


class RenameBody(BaseModel):
    old: str
    new: str
    name: str | None = None


@router.post("/rename-symbol")
def rename_symbol(body: RenameBody, admin: dict = Depends(require_admin)):
    """Owner-confirmed ticker-change repair (the AVB→VMRK machinery).

    Renames the ledger's execution rows, trues each member's share count to the
    live account with an append-only $0 adjustment, and swaps the universe row
    so the new ticker is votable. The panel's `drift` list is what surfaces the
    candidates; this is the one-click acceptance."""
    from ..services import renames
    try:
        return renames.migrate(body.old, body.new, name=body.name,
                               actor=f"admin:{admin['id']}")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/bots")
def bots_status(admin: dict = Depends(require_admin)):
    """The bot roster (power/accuracy/last picks) + whether their LLM is up.

    The probe is a 2s GET against the Ollama base URL — on the live box that's
    the SSH tunnel to Forge, so "unreachable" here almost always means the
    tunnel dropped, not that the models broke. Kept out of /panel: the panel is
    a DB-only fast path shared with the morning digest timer, and this row adds
    a network wait.
    """
    return {"bots": bots.bots_view(), "llm": ollama.probe()}


@router.get("/overview")
def overview(admin: dict = Depends(require_admin)):
    with db() as conn:
        members = conn.execute(
            "SELECT m.id,m.email,m.display_name,m.role,m.last_login_at,m.created_at,"
            "m.is_bot,m.bot_strategy,m.bot_model,"
            "s.opted_in,s.voting_power,a.account_number,a.agentic_allowed,a.status AS rh_status,"
            "(SELECT delta FROM voting_power_history h WHERE h.member_id=m.id "
            "  ORDER BY h.week_start DESC LIMIT 1) AS power_delta "
            "FROM members m LEFT JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id ORDER BY s.voting_power DESC, m.id"
        ).fetchall()
    session = svc.active_session()
    sess_view = svc.session_public(session, admin["id"]) if session else None
    execs = []
    if session:
        with db() as conn:
            rows = conn.execute(
                "SELECT e.*, m.display_name FROM executions e JOIN members m ON m.id=e.member_id "
                "WHERE e.session_id=? ORDER BY e.member_id,e.symbol", (session["id"],)
            ).fetchall()
        execs = [dict(r) for r in rows]
    mdicts = [dict(m) for m in members]
    total_power = sum(float(m["voting_power"] or 0) for m in mdicts)
    bot_power = sum(float(m["voting_power"] or 0) for m in mdicts if m["is_bot"])
    return {
        "armed": is_armed(),
        "halt": kv_get("halt") == "1",
        "execution_mode": settings.execution_mode,
        "top_n": settings.top_n,
        "total_voting_power": round(total_power, 2),
        "bot_voting_power": round(bot_power, 2),
        "human_count": sum(1 for m in mdicts if not m["is_bot"]),
        "bot_count": sum(1 for m in mdicts if m["is_bot"]),
        # Owner-only (require_owner + the proxy's independent owner auth): the
        # shareable invite code + owner email, surfaced so the forge dashboard can
        # display them. NOTE: password recovery is owner-mediated ONLY
        # (POST /member/{id}/reset-password); there is deliberately no invite-code
        # self-serve reset — a shared-invite reset would let any member reset
        # another member's account.
        "invite_code": settings.invite_code,
        "owner_email": settings.owner_email,
        "schedule": {"open": settings.vote_open_at, "close": settings.vote_close_at,
                     "execute": settings.execute_at, "weekly_power": settings.weekly_power_at,
                     "cadence": settings.cycle_cadence},
        "members": mdicts,
        "session": sess_view,
        "executions": execs,
    }


@router.get("/member/{member_id}")
def member_detail(member_id: int, admin: dict = Depends(require_admin)):
    """Everything about one member: live holdings, what they bought, power history."""
    base = load_member(member_id)
    if not base:
        raise HTTPException(404, "no such member")
    out: dict = {"member": base, "power": power.member_power_view(member_id),
                 "allocation": alloc.member_allocation(member_id), "pnl": alloc.club_pnl(member_id)}

    session = svc.active_session()
    if session:
        out["session_date"] = session["trade_date"]
        out["picks"] = svc.member_picks(session["id"], member_id)
        # bot extras: this session's weighted picks + thesis
        with db() as conn:
            bp = conn.execute(
                "SELECT symbol, weight_pct, entry_price, thesis FROM bot_picks "
                "WHERE session_id=? AND member_id=? ORDER BY weight_pct DESC",
                (session["id"], member_id)
            ).fetchall()
        if bp:
            out["bot_picks"] = [dict(r) for r in bp]
            out["bot_thesis"] = bp[0]["thesis"]
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


@router.post("/member/{member_id}/reset-password")
def reset_password(member_id: int, admin: dict = Depends(require_admin)):
    """Admin-mediated recovery for a member who forgot their password.

    Generates a random temporary password, stores its hash, and invalidates the
    member's existing sessions (bump session_version). Returns the temp password
    ONCE for the admin to relay; the member logs in with it and can change it.
    Admin-mediated by design — a shared-invite self-serve reset would let any
    member reset another member's account. The OWNER's own password stays
    owner-only: a cofounder resetting it would be an owner-account takeover.
    """
    import secrets
    from ..security import hash_password
    with db() as conn:
        row = conn.execute("SELECT id,email,role,is_bot FROM members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such member")
        if row["is_bot"]:
            raise HTTPException(400, "bots have no login")
        if row["role"] == "owner" and admin.get("role") != "owner":
            raise HTTPException(403, "only the owner can reset the owner's password")
        temp = secrets.token_urlsafe(9)  # ~12 chars, > 8 min
        conn.execute(
            "UPDATE members SET password_hash=?, session_version=session_version+1 WHERE id=?",
            (hash_password(temp), member_id),
        )
    audit(admin["email"], "reset_password", row["email"], None)
    return {"ok": True, "email": row["email"], "temp_password": temp}


@router.post("/member/{member_id}/rh-reset")
def rh_reset(member_id: int, admin: dict = Depends(require_admin)):
    """Un-strand a member whose rh_accounts.status is stuck at 'error'.

    A transient failure used to strand members permanently; now only auth
    failures mark 'error', and this flips status back to 'connected' — but only
    when an encrypted token bundle is still stored (no tokens → the member must
    re-OAuth, resetting the flag would be a lie).
    """
    try:
        res = vault.reset_status(member_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "cannot reset")
    audit(admin["email"], "rh_reset", str(member_id),
          {"previous_status": res.get("previous_status")})
    return {"ok": True, "member_id": member_id, "status": "connected",
            "previous_status": res.get("previous_status")}


@router.post("/member/{member_id}/dust-cleanup")
def dust_cleanup(member_id: int, dry_run: bool = True, admin: dict = Depends(require_admin)):
    """Sweep sub-$1 fill-drift crumbs on club-traded names for one member.

    dry_run=true (default) returns the preview without recording or placing;
    dry_run=false places only when execution_mode=live AND ARMED (same gate as
    the executor).
    """
    if not load_member(member_id):
        raise HTTPException(404, "no such member")
    res = executor.dust_cleanup(member_id, dry_run=dry_run)
    audit(admin["email"], "dust_cleanup", str(member_id),
          {"dry_run": dry_run, "ok": res.get("ok"),
           "orders": [{"symbol": o.get("symbol"), "status": o.get("status")}
                      for o in res.get("orders", [])]})
    return res


@router.post("/member/{member_id}/reconcile")
def reconcile_member(member_id: int, dry_run: bool = True, admin: dict = Depends(require_admin)):
    """Reconcile a member's placed executions to their REAL broker fills.

    Rewrites estimate-based quantities to actual fills so club_shares is truth-
    based (the "not all shares get sold" fix), promotes errored-but-filled orders,
    and demotes broker-rejected rows. Places NO orders. dry_run=true (default)
    previews the changes without writing.
    """
    if not load_member(member_id):
        raise HTTPException(404, "no such member")
    res = reconcile.reconcile_fills(member_id, dry_run=dry_run)
    audit(admin["email"], "reconcile", str(member_id),
          {"dry_run": dry_run, "changed": res.get("changed"),
           "promoted": len(res.get("promoted", [])), "demoted": len(res.get("demoted", []))})
    return res


@router.post("/reconcile")
def reconcile_all_members(dry_run: bool = True, admin: dict = Depends(require_admin)):
    """Reconcile every connected member's fills (no orders placed). dry_run previews."""
    res = reconcile.reconcile_all(dry_run=dry_run)
    audit(admin["email"], "reconcile_all", None, {"dry_run": dry_run})
    return res


@router.get("/api-keys")
def admin_list_keys(owner: dict = Depends(require_owner)):
    """The owner's API keys (for the research-voter program), creatable from forge.

    OWNER-ONLY (not require_admin): these keys authenticate AS the owner member,
    so minting/revoking/listing them is owner-identity-bound — a cofounder with
    them could vote and act as the owner.
    """
    mid = _owner_mid()
    return {"member_id": mid, "keys": apikeys.list_keys(mid)}


@router.post("/api-keys")
def admin_create_key(body: ApiKeyBody, owner: dict = Depends(require_owner)):
    return apikeys.create_key(_owner_mid(), body.name)


@router.delete("/api-keys/{key_id}")
def admin_revoke_key(key_id: int, owner: dict = Depends(require_owner)):
    if not apikeys.revoke_key(_owner_mid(), key_id):
        raise HTTPException(404, "no such key")
    return {"ok": True}


@router.post("/program/research-vote")
def admin_research_vote(body: ProgramVoteBody, owner: dict = Depends(require_owner)):
    """Run the research-voter as the owner (forge 'Run now'). dry_run previews.

    OWNER-ONLY (not require_admin): this casts a ballot AS the owner member —
    and `symbols` can override the basket, so an admin could vote arbitrary
    names under the owner's identity/weight.
    """
    return research.research_vote(_owner_mid(), override=body.symbols, dry=body.dry_run)


class SeedResearchBody(BaseModel):
    research: list[dict] | None = None   # [{symbol, appearances}]
    symbols: list[str] | None = None


@router.post("/program/seed-research")
def admin_seed_research(body: SeedResearchBody, owner: dict = Depends(require_owner)):
    """Seed the owner's research snapshot (fallback when a live watchlist read fails).

    OWNER-ONLY (not require_admin): the snapshot feeds the owner's ballot, so
    seeding it steers what gets voted under the owner's identity.
    """
    if body.research:
        data = [{"symbol": str(r.get("symbol", "")).upper(), "appearances": int(r.get("appearances") or 1)}
                for r in body.research if r.get("symbol")]
    elif body.symbols:
        data = [{"symbol": s.upper(), "appearances": 1} for s in body.symbols]
    else:
        raise HTTPException(400, "provide research or symbols")
    research.set_snapshot(_owner_mid(), data)
    return {"ok": True, "count": len(data), "research": data}


@router.post("/arm")
def arm(body: ArmBody, admin: dict = Depends(require_admin)):
    set_armed(body.armed, actor=admin["email"])
    if body.armed:
        kv_set("halt", "0")
    return {"armed": is_armed()}


@router.post("/kill")
def kill(admin: dict = Depends(require_admin)):
    kv_set("halt", "1")
    set_armed(False, actor=admin["email"])
    audit(admin["email"], "kill", None, None)
    return {"armed": False, "halt": True}


@router.post("/run/{phase}")
def run_phase(phase: str, dry_run: bool = True, admin: dict = Depends(require_admin)):
    """Manually trigger a phase. `execute` honours dry_run unless live+armed."""
    if phase not in ("open", "close", "execute", "power", "bots", "reweight"):
        raise HTTPException(400, "phase must be open|close|execute|power|bots|reweight")
    # EVERY manual trigger audits WHO pulled the lever BEFORE the phase runs —
    # arm/kill/reset-password were audited while `run` (the lever that can move
    # money) was not. Before, not after: a phase that crashes mid-flight must
    # still leave the fingerprint.
    audit(admin["email"], "manual_trigger", phase, {"dry_run": dry_run})
    if phase == "open":
        return {"session": svc.ensure_active_session()}
    if phase == "power":
        return {"snapshots": power.snapshot_all()}
    if phase == "bots":          # seed (if needed) + have every bot vote now
        bots.seed_bots()
        return bots.cast_votes(svc.ensure_active_session())
    if phase == "reweight":      # weekly accuracy → bot voting power
        return bots.score_and_reweight()
    if phase == "close":
        # close TODAY's ballot (active_session can be the next day's after a close)
        session = svc.session_for_trade_date(today_et().isoformat()) or svc.active_session()
        if not session:
            raise HTTPException(404, "no session")
        return {"session": svc.close_and_select(session["id"])}
    # phase == "execute":
    # Do NOT clear halt here (finding #15) — if the club is halted, execution
    # stays blocked until the owner explicitly re-arms. run_today runs the
    # phase scheduled for today (sell on a sell day, buy on a buy day),
    # honouring the split-cycle cadence, not a same-day full rebalance.
    # trigger=manual:<email> stamps the run_reports row with who fired it.
    return executor.run_today(force_dry_run=dry_run, trigger=f"manual:{admin['email']}")
