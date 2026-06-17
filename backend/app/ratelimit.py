"""In-process rate limiting for auth endpoints.

concord-api runs as a single uvicorn worker, so an in-memory sliding window is
sufficient (and avoids a Redis dependency). Keyed by email (account lockout,
robust regardless of source IP) and by client IP.
"""
from __future__ import annotations

import time

_hits: dict[str, list[float]] = {}


def too_many(key: str, limit: int, window: int) -> bool:
    """True if `key` already has >= limit hits within the last `window` seconds."""
    now = time.time()
    arr = [t for t in _hits.get(key, []) if now - t < window]
    _hits[key] = arr
    return len(arr) >= limit


def record(key: str) -> None:
    _hits.setdefault(key, []).append(time.time())
    # opportunistic cleanup so the dict can't grow unbounded
    if len(_hits) > 5000:
        cutoff = time.time() - 3600
        for k in list(_hits):
            _hits[k] = [t for t in _hits[k] if t > cutoff]
            if not _hits[k]:
                del _hits[k]


def clear(key: str) -> None:
    _hits.pop(key, None)
