"""Publish the REAL club's shared record for the demo instance to read.

Runs against the LIVE database (this job is installed on the production
instance) and writes exactly two payloads to a file the demo can read:

    {"mirrored_at": epoch, "history": [...], "performance": {...}}

Both are club-level and non-identifying by construction — club_history is the
same bytes for every member (tests/test_history_endpoints.py asserts it) and the
performance series is an aggregate index vs SPY. Nothing member-scoped is
written: no emails, names, member ids, votes, holdings or tokens.

This direction of travel is deliberate. The alternative — pointing the demo
backend at the live database read-only — would put a public sign-up one bug away
from members' rows and encrypted broker tokens. Here the demo only ever reads a
file containing data it was already entitled to serve.

Run from the PRODUCTION backend/:
    python -m jobs.mirror_club --out /var/lib/concord-mirror/club.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

from app.config import settings
from app.services import performance, voting

# Member-scoped OR operational keys must never appear in a mirrored row.
# club_history is not supposed to emit any of these; this is a belt-and-braces
# check so a future change to that payload cannot silently start leaking into a
# public demo. voters/human_voters/orders joined the list with the member/admin
# split: the member-facing record is date · status · basket only, and voter
# counts + order tallies are admin-console material (GET /api/admin/cycles).
_FORBIDDEN = {"email", "display_name", "member_id", "members", "name", "votes_by",
              "holdings", "account_number", "token", "password_hash",
              "voters", "human_voters", "orders"}


def _assert_clean(rows: list[dict]) -> None:
    for row in rows:
        bad = _FORBIDDEN.intersection(row.keys())
        if bad:
            raise SystemExit(f"refusing to mirror: club history row carries member-scoped keys {sorted(bad)}")


def build(limit: int = 60) -> dict:
    if settings.demo_mode:
        raise SystemExit("refusing to run on a demo instance — this mirrors FROM the real club")
    history = voting.club_history(limit)
    _assert_clean(history)
    perf = performance.club_performance()
    # The LIVE ballot's aggregate tally (symbol/name/votes/weight + counts) —
    # no member identity, same numbers every real member already sees on their
    # own Vote page. The demo overlays it so its consensus reflects the club
    # actually deciding (owner: "show the votes coming from the live, since
    # all decisions are actually coming from there").
    today = None
    open_row = voting.latest_session("open")
    if open_row and (open_row.get("closes_at") or 0) > time.time():
        pub = voting.session_public(open_row, member_id=None)
        # The mirror's contract: NOTHING member-identifying crosses to the demo
        # box. session_public now attaches theses [{name, text}] for the member
        # UI — strip them (and refuse surprises) before the tally is exported.
        for _row in (pub.get("tally") or []):
            _row.pop("theses", None)
        today = {"trade_date": pub.get("trade_date"),
                 "tally": pub.get("tally") or [],
                 "voters": pub.get("voters") or 0}
    return {"mirrored_at": int(time.time()), "history": history,
            "performance": perf, "today": today}


def write(path: str, payload: dict) -> None:
    """Atomic replace, so a reader never sees a half-written file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    data = build(args.limit)
    write(args.out, data)
    n = len(data["history"])
    pts = len((data["performance"] or {}).get("points") or [])
    print(f"[mirror_club] {n} cycles, {pts} perf points -> {args.out}", file=sys.stderr)
    print(json.dumps({"cycles": n, "perf_points": pts, "out": args.out}))
