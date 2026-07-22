"""Bridges the encrypted token store (DB) and the live Robinhood client.

- save_connection / load_bundle: encrypted-at-rest per-member token bundles.
- ensure_client: returns a ready RobinhoodClient, transparently refreshing the
  access token (and re-persisting) when stale — mirrors refresh.py freshness.
- pending OAuth state: short-lived {verifier,state,client_id} kept in kv, keyed
  to the member, to verify the callback (CSRF via `state`, PKCE via `verifier`).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time

from ..db import db, kv_get, kv_set, audit
from ..security import encrypt_tokens, decrypt_tokens
from . import oauth
from .client import RHAuthError, RobinhoodClient, find_agentic_account

_PENDING_TTL = 900  # 15 min to complete the Robinhood consent


# ─── pending OAuth (CSRF + PKCE), keyed by `state` ───────────────────────────
# The Robinhood callback is a top-level browser redirect with no Authorization
# header, so we identify the member from the opaque `state` we issued at start.

def set_pending_state(state: str, member_id: int, verifier: str, client_id: str) -> None:
    kv_set(f"rh_state:{state}", json.dumps({
        "member_id": member_id, "verifier": verifier, "client_id": client_id,
        "ts": int(time.time()),
    }))


def take_pending_state(state: str) -> dict | None:
    raw = kv_get(f"rh_state:{state}")
    if not raw:
        return None
    with db() as conn:
        conn.execute("DELETE FROM kv WHERE key=?", (f"rh_state:{state}",))
    data = json.loads(raw)
    if int(time.time()) - data.get("ts", 0) > _PENDING_TTL:
        return None
    return data


# ─── token bundles ───────────────────────────────────────────────────────────

def save_connection(member_id: int, bundle: dict, account_number: str | None,
                    agentic_allowed: bool) -> None:
    enc = encrypt_tokens(bundle)
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,enc_tokens,"
            "connected_at,last_refresh_at,status) VALUES(?,?,?,?,?,?,'connected') "
            "ON CONFLICT(member_id) DO UPDATE SET account_number=excluded.account_number,"
            "agentic_allowed=excluded.agentic_allowed,enc_tokens=excluded.enc_tokens,"
            "last_refresh_at=excluded.last_refresh_at,status='connected'",
            (member_id, account_number, 1 if agentic_allowed else 0, enc, now, now),
        )
    audit(f"member:{member_id}", "rh_connect", account_number,
          {"agentic_allowed": agentic_allowed})


def load_bundle(member_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT enc_tokens FROM rh_accounts WHERE member_id=? AND status='connected'",
            (member_id,),
        ).fetchone()
    if not row or not row["enc_tokens"]:
        return None
    return decrypt_tokens(row["enc_tokens"])


def account_number_for(member_id: int) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT account_number FROM rh_accounts WHERE member_id=?", (member_id,)
        ).fetchone()
    return row["account_number"] if row else None


def _persist_refreshed(member_id: int, bundle: dict) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE rh_accounts SET enc_tokens=?, last_refresh_at=? WHERE member_id=?",
            (encrypt_tokens(bundle), int(time.time()), member_id),
        )


def _refresh_lock(member_id: int):
    """Advisory per-member lock file for serializing the token refresh (below)."""
    path = os.path.join(tempfile.gettempdir(), f"concord-rhrefresh-{member_id}.lock")
    try:
        return open(path, "w")  # noqa: SIM115 — held only for the refresh critical section
    except OSError:
        return None


def ensure_client(member_id: int) -> RobinhoodClient:
    """Return a live client for the member, refreshing the access token if stale.

    Refresh is serialized PER MEMBER with DOUBLE-CHECKED LOCKING. Robinhood rotates
    agentic refresh tokens, so two concurrent refreshes of the same member (there are
    ~14 ensure_client call sites across the web app + timer jobs, and the daily
    schedules overlap) redeem the SAME refresh token — RH's rotation-reuse detection
    then revokes the whole token family, including the one the winner just persisted,
    leaving a dead bundle that only a full re-OAuth fixes. That is the systemic cause
    of members being forced to reconnect. The lock + re-load-inside-the-lock makes the
    race-loser adopt the winner's freshly-rotated token instead of redeeming a spent
    one. The fresh-token fast path takes NO lock.
    """
    bundle = load_bundle(member_id)
    if not bundle:
        raise RuntimeError("member has no connected Robinhood account")
    if oauth.is_access_fresh(bundle):
        return RobinhoodClient(bundle["access_token"])

    lf = _refresh_lock(member_id)
    try:
        if lf is not None:
            fcntl.flock(lf, fcntl.LOCK_EX)      # block concurrent refreshers of THIS member
            # double-check: the refresher we raced may have just persisted a fresh token
            reloaded = load_bundle(member_id)
            if reloaded:
                bundle = reloaded
                if oauth.is_access_fresh(bundle):
                    return RobinhoodClient(bundle["access_token"])
        # we hold the lock and the stored token is still stale → we are the refresher
        try:
            bundle = oauth.refresh_bundle(bundle)
        except Exception as e:
            # a 400 may be a rotation race won by a non-locked path — re-check the store
            # once; if it now holds a fresh token, adopt it rather than stranding.
            reloaded = load_bundle(member_id)
            if reloaded and oauth.is_access_fresh(reloaded):
                return RobinhoodClient(reloaded["access_token"])
            if isinstance(e, RHAuthError):
                # RH rejected the refresh token itself (permanent) — mark the dead
                # connection NOW so the member UI shows needs-reauth immediately,
                # not a healthy account until the executor's next trade cycle.
                # Anything transient falls through unmarked: a blip never strands.
                mark_status(member_id, "error")
                audit("system", "rh_reauth_required", str(member_id), {"err": str(e)[:200]})
            raise
        _persist_refreshed(member_id, bundle)
        return RobinhoodClient(bundle["access_token"])
    finally:
        if lf is not None:
            try:
                fcntl.flock(lf, fcntl.LOCK_UN)
            finally:
                lf.close()


def complete_connection(member_id: int, bundle: dict) -> dict:
    """After token exchange: discover the agentic account and persist. Returns info."""
    client = RobinhoodClient(bundle["access_token"])
    accounts = client.get_accounts()
    acct = find_agentic_account(accounts)
    account_number = (acct or {}).get("account_number") or (acct or {}).get("account") or None
    agentic = bool(acct and acct.get("agentic_allowed") in (True, "true", 1))
    save_connection(member_id, bundle, account_number, agentic)
    return {"account_number": account_number, "agentic_allowed": agentic}


def mark_status(member_id: int, status: str) -> None:
    with db() as conn:
        conn.execute("UPDATE rh_accounts SET status=? WHERE member_id=?", (status, member_id))


def reset_status(member_id: int) -> dict:
    """Un-strand a member whose rh_accounts.status got stuck at 'error'.

    Only flips status back to 'connected' when an encrypted token bundle is
    still stored — without tokens the member genuinely needs a fresh OAuth.
    Returns {ok, previous_status}; raises LookupError when there is no
    rh_accounts row at all.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT status, enc_tokens FROM rh_accounts WHERE member_id=?", (member_id,)
        ).fetchone()
        if not row:
            raise LookupError("member has no Robinhood account record")
        if not row["enc_tokens"]:
            return {"ok": False, "previous_status": row["status"],
                    "error": "no stored tokens — member must reconnect via OAuth"}
        conn.execute(
            "UPDATE rh_accounts SET status='connected' WHERE member_id=?", (member_id,)
        )
    return {"ok": True, "previous_status": row["status"]}
