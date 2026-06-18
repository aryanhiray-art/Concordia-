"""Encryption (token vault) and signed sessions.

- Token vault: Fernet (AES128-CBC + HMAC) over the JSON token bundle. The key
  lives only in the chmod-600 .env / systemd environment of the `concord` user.
- Sessions: a compact `<b64url(payload)>.<hexsig>` token, HMAC-SHA256 signed,
  mirroring the forge-dashboard session pattern but carrying the member id +
  a session_version (bump to revoke all of a member's sessions).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

# ─── Passwords (PBKDF2-HMAC-SHA256, stdlib) ──────────────────────────────────

_PBKDF2_ITERS = 240_000


def hash_password(password: str) -> str:
    """Return a self-describing 'pbkdf2$iter$salt$hash' string. Never stores plaintext."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("pbkdf2$"):
        return False
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$", 3)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
    except Exception:
        return False
    return hmac.compare_digest(dk, expected)

# ─── API keys (for external programs that vote on a member's behalf) ──────────

API_KEY_PREFIX = "ck_live_"


def new_api_key() -> tuple[str, str, str]:
    """Mint a key. Returns (full_key, prefix, key_hash).

    The full key is shown to the user exactly once; we persist only `prefix`
    (for display/lookup) and `key_hash` (sha256). Format: ck_live_<token>.
    """
    full = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return full, full[:16], hash_api_key(full)


def hash_api_key(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()

# ─── Token vault ─────────────────────────────────────────────────────────────


def _fernet() -> Fernet:
    key = settings.fernet_key
    if not key:
        raise RuntimeError(
            "CONCORD_FERNET_KEY is not set — refusing to handle Robinhood tokens. "
            "Generate one: python -c \"from cryptography.fernet import Fernet;"
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_tokens(bundle: dict) -> bytes:
    """Encrypt a token bundle (access/refresh/client_id/meta) to bytes at rest."""
    return _fernet().encrypt(json.dumps(bundle, separators=(",", ":")).encode())


def decrypt_tokens(blob: bytes) -> dict:
    return json.loads(_fernet().decrypt(blob).decode())


# ─── Signed sessions ─────────────────────────────────────────────────────────

SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def ct_eq(a: str | None, b: str | None) -> bool:
    """Constant-time string compare (avoids timing oracles on secrets)."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def _sign(msg: str) -> str:
    return hmac.new(settings.session_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(member_id: int, version: int, ttl: int = SESSION_TTL_SECONDS) -> str:
    payload = {"mid": member_id, "ver": version, "exp": int(time.time()) + ttl}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{body}.{_sign(body)}"


def read_session(token: str | None) -> dict | None:
    """Return the payload dict if the token is valid + unexpired, else None."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode())
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload
