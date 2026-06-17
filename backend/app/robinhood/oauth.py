"""Robinhood Agentic Trading OAuth — per member, public client, PKCE S256.

Generalizes forge-dashboard/jobs/robinhood_refresh/auth_setup.py for a web app:
the single-owner localhost:8917 SSH-tunnel redirect is replaced with a public
HTTPS callback (settings.rh_redirect_uri). Each member runs the flow in their
own browser against their own Robinhood login; we capture per-member tokens.

Flow:
  1. register_client()  — dynamic client registration with OUR callback (cached)
  2. start_authorization(member_id) — mint PKCE pair + state, return authorize URL
  3. exchange_code(code, verifier)  — code → {access,refresh,client_id,...}
  4. refresh_bundle(bundle)         — refresh_token grant when the access stale
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time

import requests

from ..config import settings
from ..db import kv_get, kv_set

_TIMEOUT = 30


def make_pkce() -> tuple[str, str]:
    """Return (verifier, challenge) using S256, matching auth_setup.py."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def register_client() -> str:
    """Dynamic client registration (public client) with our callback. Cached in kv."""
    if settings.rh_client_id_env:
        return settings.rh_client_id_env
    cached = kv_get("rh_client_id")
    if cached:
        return cached
    # Robinhood dedupes dynamic registration by client_name, returning a prior
    # client bound to its original redirect. Scope the name to THIS deployment's
    # host so a fresh client registers against the current redirect_uri.
    host = settings.api_public_url.split("://", 1)[-1].rstrip("/")
    reg = requests.post(
        settings.rh_register_url,
        json={
            "client_name": f"Concord daily-vote trading club @ {host}",
            "redirect_uris": [settings.rh_redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": settings.rh_scope,
        },
        timeout=_TIMEOUT,
    )
    if reg.status_code not in (200, 201):
        raise RuntimeError(f"RH client registration failed {reg.status_code}: {reg.text[:300]}")
    client_id = reg.json()["client_id"]
    kv_set("rh_client_id", client_id)
    return client_id


def build_authorize_url(client_id: str, state: str, challenge: str) -> str:
    import urllib.parse
    return settings.rh_authorize_url + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.rh_redirect_uri,
        "scope": settings.rh_scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": settings.rh_resource,
    })


def start_authorization() -> dict:
    """Return everything needed to send a member to Robinhood and verify the return.

    Caller persists {verifier, state} server-side (keyed to the member), then
    redirects the member to authorize_url.
    """
    client_id = register_client()
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)
    return {
        "client_id": client_id,
        "verifier": verifier,
        "state": state,
        "authorize_url": build_authorize_url(client_id, state, challenge),
    }


def exchange_code(code: str, verifier: str, client_id: str) -> dict:
    tok = requests.post(
        settings.rh_token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.rh_redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": settings.rh_resource,
        },
        timeout=_TIMEOUT,
    )
    if tok.status_code != 200:
        raise RuntimeError(f"RH token exchange failed {tok.status_code}: {tok.text[:300]}")
    bundle = tok.json()
    bundle["client_id"] = client_id
    bundle["obtained_at"] = int(time.time())
    return bundle


def refresh_bundle(bundle: dict) -> dict:
    """Refresh-token grant — mirrors refresh.py:get_access_token."""
    r = requests.post(
        settings.rh_token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": bundle["refresh_token"],
            "client_id": bundle["client_id"],
            "resource": settings.rh_resource,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    new = r.json()
    bundle.update(new)
    bundle["obtained_at"] = int(time.time())
    return bundle


def is_access_fresh(bundle: dict) -> bool:
    age = time.time() - bundle.get("obtained_at", 0)
    return age < max(60, int(bundle.get("expires_in", 3600)) - 300)
