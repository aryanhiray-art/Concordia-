"""Robinhood connect flow (per member)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..config import settings
from ..db import audit, db
from ..robinhood import oauth, vault
from .deps import require_member

router = APIRouter(tags=["robinhood"])


@router.get("/api/oauth/robinhood/start")
def start(member: dict = Depends(require_member)):
    info = oauth.start_authorization()
    vault.set_pending_state(info["state"], member["id"], info["verifier"], info["client_id"])
    audit(f"member:{member['id']}", "rh_oauth_start", None, None)
    return {"authorize_url": info["authorize_url"]}


@router.get("/oauth/robinhood/callback")
def callback(request: Request):
    """Public Robinhood redirect target. Identifies the member via `state`."""
    params = request.query_params
    web = settings.web_origin.rstrip("/")
    if params.get("error"):
        return RedirectResponse(f"{web}/connected?status=error&reason={params.get('error')}")
    code, state = params.get("code"), params.get("state")
    pending = vault.take_pending_state(state) if state else None
    if not code or not pending:
        return RedirectResponse(f"{web}/connected?status=error&reason=invalid_state")
    try:
        bundle = oauth.exchange_code(code, pending["verifier"], pending["client_id"])
        info = vault.complete_connection(pending["member_id"], bundle)
    except Exception as e:  # noqa: BLE001
        audit(f"member:{pending['member_id']}", "rh_oauth_error", None, {"err": str(e)})
        return RedirectResponse(f"{web}/connected?status=error&reason=exchange_failed")
    return RedirectResponse(
        f"{web}/connected?status=ok&account={info.get('account_number') or ''}"
        f"&agentic={'1' if info.get('agentic_allowed') else '0'}"
    )


@router.post("/api/oauth/robinhood/disconnect")
def disconnect(member: dict = Depends(require_member)):
    with db() as conn:
        conn.execute("UPDATE rh_accounts SET status='revoked', enc_tokens=NULL WHERE member_id=?",
                     (member["id"],))
    audit(f"member:{member['id']}", "rh_disconnect", None, None)
    return {"ok": True}
