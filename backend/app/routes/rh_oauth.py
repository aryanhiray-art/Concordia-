"""Robinhood connect flow (per member).

Robinhood's agentic OAuth only honours a *localhost* redirect, so after the
member approves, their browser lands on a localhost "can't connect" page whose
URL carries ?code=&state=. They paste that URL back here (`/complete`) and we
exchange it server-side. The hosted `/callback` route is kept as a fallback in
case Robinhood ever redirects to a hosted URL.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import settings
from ..db import audit, db
from ..robinhood import oauth, vault
from ..services import power
from .deps import require_member

router = APIRouter(tags=["robinhood"])


@router.get("/api/oauth/robinhood/start")
def start(member: dict = Depends(require_member)):
    if settings.demo_mode:
        # DEMO: the "Robinhood" tab is our own stage prop; the paste-back flow
        # stays identical so the member experiences the real choreography.
        audit(f"member:{member['id']}", "rh_oauth_start", None, {"demo": True})
        return {"authorize_url": f"{settings.api_public_url}/demo/robinhood"}
    info = oauth.start_authorization()
    vault.set_pending_state(info["state"], member["id"], info["verifier"], info["client_id"])
    audit(f"member:{member['id']}", "rh_oauth_start", None, None)
    return {"authorize_url": info["authorize_url"]}


@router.get("/demo/robinhood")
def demo_robinhood():
    """DEMO_MODE only: the pretend Robinhood approval page."""
    from fastapi.responses import HTMLResponse
    if not settings.demo_mode:
        raise HTTPException(status_code=404)
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8">
<title>Robinhood (demo)</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;
color:#fff;font-family:-apple-system,system-ui,sans-serif}.c{max-width:420px;padding:40px;text-align:center}
h1{color:#00C805;font-size:22px}p{color:#9aa;line-height:1.6;font-size:14px}
code{background:#111;border:1px solid #222;padding:8px 12px;border-radius:8px;display:block;
margin-top:14px;color:#0f8;font-size:12px;word-break:break-all}</style></head><body><div class="c">
<h1>Robinhood — demo approval</h1>
<p>Pretend you just logged in and tapped <b style="color:#fff">Approve</b>.<br><br>
In the real flow you would now land on a &ldquo;can&rsquo;t connect&rdquo; localhost page.
Copy THIS page&rsquo;s URL from the address bar (or the one below) and paste it back into Concordia.</p>
<code>http://localhost:8917/callback?code=demo&amp;state=demo</code>
</div></body></html>""")


class CompleteBody(BaseModel):
    redirect_url: str


def _finish(member_id: int, code: str, state: str) -> dict:
    pending = vault.take_pending_state(state)
    if not pending:
        raise HTTPException(status_code=400,
                            detail="this approval expired or was already used — click Connect again")
    if pending["member_id"] != member_id:
        raise HTTPException(status_code=403, detail="that approval belongs to a different account")
    try:
        bundle = oauth.exchange_code(code, pending["verifier"], pending["client_id"])
        info = vault.complete_connection(member_id, bundle)
    except Exception as e:  # noqa: BLE001
        audit(f"member:{member_id}", "rh_oauth_error", None, {"err": str(e)})
        raise HTTPException(status_code=502, detail="Robinhood token exchange failed — try connecting again")
    try:
        power.snapshot_member(member_id)
    except Exception:  # noqa: BLE001 — never fail the connect over a snapshot
        pass
    return {"ok": True, "account_number": info.get("account_number"),
            "agentic_allowed": bool(info.get("agentic_allowed"))}


@router.post("/api/oauth/robinhood/complete")
def complete(body: CompleteBody, member: dict = Depends(require_member)):
    """Member pastes the URL Robinhood redirected them to; we extract code+state."""
    if settings.demo_mode:
        # DEMO: any non-empty paste "connects" a fake agentic account.
        if not (body.redirect_url or "").strip():
            raise HTTPException(status_code=400, detail="paste the URL from the demo Robinhood page")
        import time as _time
        from ..robinhood.demo import DemoClient
        vault.save_connection(member["id"], {
            "access_token": "demo", "refresh_token": "demo", "client_id": "demo",
            "obtained_at": int(_time.time()), "expires_in": 10 * 365 * 86400,
        }, DemoClient.DEMO_ACCOUNT, True)
        try:
            power.snapshot_member(member["id"])
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "account_number": DemoClient.DEMO_ACCOUNT, "agentic_allowed": True}
    raw = (body.redirect_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="paste the URL Robinhood sent you after approving")
    query = urlparse(raw).query or (raw.split("?", 1)[1] if "?" in raw else raw)
    params = parse_qs(query)
    if params.get("error"):
        raise HTTPException(status_code=400, detail=f"Robinhood returned an error: {params['error'][0]}")
    code = (params.get("code") or [None])[0]
    state = (params.get("state") or [None])[0]
    if not code or not state:
        raise HTTPException(status_code=400,
                            detail="that URL is missing code/state — copy the full address bar after approving")
    return _finish(member["id"], code, state)


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
        # give them voting power immediately = their current equity
        try:
            power.snapshot_member(pending["member_id"])
        except Exception:  # noqa: BLE001 — never fail the connect over a snapshot
            pass
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
