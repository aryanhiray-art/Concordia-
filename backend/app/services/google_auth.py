"""Google ID-token verification for Sign in with Google.

Verification goes through Google's tokeninfo endpoint
(GET https://oauth2.googleapis.com/tokeninfo?id_token=...) — Google's supported
server-side way to validate an ID token without shipping a JWT/JWKS crypto
stack. One HTTPS round-trip per sign-in is fine at club scale; at growth, swap
this for local signature verification against Google's published JWKS keys
(cached), which removes the per-login network call.

Fail closed: anything short of a 200 response with the right audience, issuer,
a verified email and an unexpired token raises ValueError (user-safe message).
"""
from __future__ import annotations

import time

import requests

from ..config import settings

_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def verify_id_token(credential: str) -> dict:
    """Verify a Google ID token (JWT) → {sub, email, email_verified, name}.

    Raises ValueError with a user-safe message on ANY failed check. The
    returned email is normalized (stripped + lowercased) and always verified.
    """
    try:
        r = requests.get(_TOKENINFO_URL, params={"id_token": credential}, timeout=10)
    except requests.RequestException:
        raise ValueError("could not reach Google to verify the sign-in — try again")
    if r.status_code != 200:
        raise ValueError("google sign-in token rejected")
    try:
        info = r.json()
    except ValueError:
        raise ValueError("google sign-in token rejected")
    if not settings.google_client_id or info.get("aud") != settings.google_client_id:
        raise ValueError("google sign-in token was issued for a different app")
    if info.get("iss") not in _ISSUERS:
        raise ValueError("google sign-in token has an unexpected issuer")
    if info.get("email_verified") not in (True, "true"):
        raise ValueError("google account email is not verified")
    try:
        exp = int(info.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    if exp <= int(time.time()):
        raise ValueError("google sign-in expired — try again")
    sub = str(info.get("sub") or "").strip()
    email = str(info.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValueError("google sign-in token rejected")
    return {"sub": sub, "email": email, "email_verified": True,
            "name": str(info.get("name") or "")}
