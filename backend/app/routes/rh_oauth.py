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
from ..services import accountsnap, power
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
    # The URL is the ONLY thing the member has to carry back, so it gets a
    # one-tap copy button — selecting a long query string by hand (especially on
    # a phone, where the address bar truncates it) was the step people stalled
    # on. Falls back to selecting the text when the Clipboard API is unavailable
    # (it needs a secure context, so plain-http local sandboxes land here).
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robinhood (demo)</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;
color:#fff;font-family:-apple-system,system-ui,sans-serif}.c{max-width:460px;padding:32px 24px;text-align:center}
h1{color:#00C805;font-size:22px;margin:0 0 14px}p{color:#9aa;line-height:1.6;font-size:14px;margin:0 0 16px}
.row{display:flex;gap:8px;align-items:stretch;margin-top:6px}
code{flex:1;min-width:0;background:#111;border:1px solid #222;padding:11px 12px;border-radius:8px;
text-align:left;color:#0f8;font-size:12px;word-break:break-all;user-select:all}
button{flex:none;padding:0 16px;border-radius:8px;border:1px solid #00C805;background:#00C805;
color:#012; font:700 13px/1 -apple-system,system-ui,sans-serif;cursor:pointer}
button:active{transform:translateY(1px)}
.ok{background:#012;color:#00C805}
@media (max-width:420px){.row{flex-direction:column}button{padding:11px 16px}}</style></head><body><div class="c">
<h1>Robinhood — demo approval</h1>
<p>Pretend you just logged in and tapped <b style="color:#fff">Approve</b>.<br><br>
In the real flow you would now land on a &ldquo;can&rsquo;t connect&rdquo; page.
Copy the URL below and paste it back into Concordia.</p>
<div class="row"><code id="u">http://localhost:8917/callback?code=demo&amp;state=demo</code>
<button id="b" type="button">Copy</button></div>
<script>
(function(){var b=document.getElementById('b'),u=document.getElementById('u');
function done(){b.textContent='Copied';b.className='ok';setTimeout(function(){b.textContent='Copy';b.className='';},1600);}
b.addEventListener('click',function(){var t=u.textContent.trim();
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done,select);}else{select();}
function select(){var r=document.createRange();r.selectNodeContents(u);
var s=getSelection();s.removeAllRanges();s.addRange(r);
try{document.execCommand('copy');done();}catch(e){b.textContent='Select + copy';}}});})();
</script>
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
    except vault.DuplicateAccount as e:
        # One brokerage account, one member — otherwise the same balance backs
        # several member rows and multiplies one person's vote. Must be its own
        # message: "token exchange failed" would send them round the loop again.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001
        audit(f"member:{member_id}", "rh_oauth_error", None, {"err": str(e)})
        raise HTTPException(status_code=502, detail="Robinhood token exchange failed — try connecting again")
    try:
        power.snapshot_member(member_id)
    except Exception:  # noqa: BLE001 — never fail the connect over a snapshot
        pass
    # The cached account view belongs to the PREVIOUS (or absent) connection —
    # drop it so the next page load rebuilds against the account just linked.
    accountsnap.invalidate(member_id)
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
        accountsnap.invalidate(member["id"])
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
        accountsnap.invalidate(pending["member_id"])
    except vault.DuplicateAccount:
        return RedirectResponse(f"{web}/connected?status=error&reason=account_already_linked")
    except Exception as e:  # noqa: BLE001
        audit(f"member:{pending['member_id']}", "rh_oauth_error", None, {"err": str(e)})
        return RedirectResponse(f"{web}/connected?status=error&reason=exchange_failed")
    return RedirectResponse(
        f"{web}/connected?status=ok&account={info.get('account_number') or ''}"
        f"&agentic={'1' if info.get('agentic_allowed') else '0'}"
    )


@router.post("/api/oauth/robinhood/disconnect")
def disconnect(member: dict = Depends(require_member)):
    # Mid-execution freeze (same gate as account deletion): between close and
    # execute the member's frozen vote weight is being turned into real orders —
    # disconnecting mid-window would defund a basket they shaped.
    from ..services import voting as _voting
    if _voting.member_in_closed_unexecuted_session(member["id"]):
        raise HTTPException(
            status_code=409,
            detail="this cycle's basket is executing — disconnect unlocks after today's run completes",
        )
    with db() as conn:
        # Disconnecting removes the committed capital, and the tally reads
        # member_settings.voting_power directly between snapshots — so the
        # weight has to go now, not at the next scheduled snapshot.
        conn.execute("UPDATE member_settings SET voting_power=0, voting_power_share=0, "
                     "last_capital_usd=0, updated_at=strftime('%s','now') WHERE member_id=?",
                     (member["id"],))
        conn.execute("UPDATE rh_accounts SET status='revoked', enc_tokens=NULL WHERE member_id=?",
                     (member["id"],))
    # Stop serving the disconnected account's cached balances/holdings.
    accountsnap.invalidate(member["id"])
    audit(f"member:{member['id']}", "rh_disconnect", None, None)
    return {"ok": True}
