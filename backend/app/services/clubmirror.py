"""The REAL club's shared record, made available to the demo instance.

Why this exists: the demo has its own database, so its club-level views (History's
past cycles, the latest decided basket, the decisions-vs-SPY graph) had nothing
to show. Seeding fixtures was worse than nothing — it invented a track record.
Owner's call: show the ACTUAL club there. A member's own trades stay simulated;
only the club's story is real.

How it crosses the boundary, and why this shape:
  * jobs/mirror_club.py runs against the LIVE database and writes exactly two
    payloads to a file — club_history(...) and club_performance().
  * The demo backend only ever READS that file. It never opens the live
    database, never holds its credentials, and cannot reach members, tokens,
    votes or executions even if the demo instance were fully compromised. The
    demo is a public sign-up; giving that process a read handle on the real
    club's DB would have been the wrong trade entirely.
  * Both payloads are already club-shared by construction: club_history is the
    same bytes for every member (there is a test asserting exactly that) and
    the performance series is an aggregate index. Nothing member-identifying
    crosses.

Fails open to local data: a missing, unreadable or malformed mirror simply means
the demo shows its own (empty) club rather than erroring.
"""
from __future__ import annotations

import json
import os
import time

from ..config import settings

# Loaded at most once per interval — the file changes on the mirror job's
# cadence, and the demo serves these endpoints on every page view.
_TTL_S = 60
_cache: dict = {"at": 0.0, "payload": None}


def enabled() -> bool:
    """Only the demo instance mirrors, and only when a path is configured."""
    return bool(settings.demo_mode and settings.club_mirror_path)


def _load() -> dict | None:
    path = settings.club_mirror_path
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def payload() -> dict | None:
    """The mirrored bundle, or None when mirroring is off/unavailable."""
    if not enabled():
        return None
    now = time.time()
    if _cache["payload"] is not None and _cache["at"] > now - _TTL_S:
        return _cache["payload"]
    data = _load()
    _cache["at"], _cache["payload"] = now, data
    return data


def history(limit: int) -> list[dict] | None:
    data = payload()
    if not data:
        return None
    rows = data.get("history")
    if not isinstance(rows, list):
        return None
    return rows[: max(1, min(int(limit), 60))]


def performance() -> dict | None:
    data = payload()
    if not data:
        return None
    perf = data.get("performance")
    return perf if isinstance(perf, dict) else None


def today() -> dict | None:
    """The live club's current aggregate ballot (tally/voters), or None."""
    data = payload()
    if not data:
        return None
    t = data.get("today")
    return t if isinstance(t, dict) and t.get("tally") else None


def mirrored_at() -> int | None:
    data = payload()
    return int(data.get("mirrored_at") or 0) or None if data else None
