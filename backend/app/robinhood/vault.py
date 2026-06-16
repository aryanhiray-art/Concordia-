"""Bridges the encrypted token store (DB) and the live Robinhood client.

- save_connection / load_bundle: encrypted-at-rest per-member token bundles.
- ensure_client: returns a ready RobinhoodClient, transparently refreshing the
  access token (and re-persisting) when stale — mirrors refresh.py freshness.
- pending OAuth state: short-lived {verifier,state,client_id} kept in kv, keyed
  to the member, to verify the callback (CSRF via `state`, PKCE via `verifier`).
"""
from __future__ import annotations

import json
import time

from ..db import db, kv_get, kv_set, audit
from ..security import encrypt_tokens, decrypt_tokens
from . import oauth
from .client import RobinhoodClient, find_agentic_account

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


def ensure_client(member_id: int) -> RobinhoodClient:
    """Return a live client for the member, refreshing the access token if stale."""
    bundle = load_bundle(member_id)
    if not bundle:
        raise RuntimeError("member has no connected Robinhood account")
    if not oauth.is_access_fresh(bundle):
        bundle = oauth.refresh_bundle(bundle)
        _persist_refreshed(member_id, bundle)
    return RobinhoodClient(bundle["access_token"])


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
