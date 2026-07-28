#!/usr/bin/env python3
"""Weekday morning (08:00 ET): mail the owner + cofounder the exception digest.

Builds the SAME picture as GET /api/admin/panel (services/adminview.py — one
shared builder, so console and email never drift): the WILL-PLACE verdict with
every blocking reason, per-member exceptions, cycle preflight (voters/quorum),
and yesterday's run by_status + verify verdict. DB-only — never touches
Robinhood.

Recipients: every non-bot member with role owner/admin. Email disabled → the
body prints to stdout (demo/dev safe). NEVER exits non-zero: a mail-provider
hiccup must not flap the timer — real-money alerting stays with forge-sentinel.

systemd: concord-admin-digest.timer → this (Mon-Fri 08:00 ET, before the 08:30
vote close + 09:31 execute, so there's time to act on what it flags).
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import adminview  # noqa: E402


def main() -> int:
    try:
        init_db()
        res = adminview.send_digest()
        if res["printed"]:
            print("[admin_digest] done (printed — email disabled)")
        else:
            print(f"[admin_digest] subject={res['subject']!r} "
                  f"sent={res['sent']} failed={res['failed']}")
    except Exception:  # noqa: BLE001 — the timer must not flap on a digest failure
        traceback.print_exc()
        print("[admin_digest] digest failed — exiting 0 so the timer doesn't flap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
