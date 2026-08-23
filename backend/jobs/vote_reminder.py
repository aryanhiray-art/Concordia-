#!/usr/bin/env python3
"""Closing-soon ballot reminder (runs late afternoon ET, daily).

systemd: concord-vote-reminder.timer → this. Sends ONE nudge per session to
participating members who have not voted, only when the open ballot closes
within the next REMIND_WINDOW hours — so on the weekly cadence (ballot closes
Monday morning) the Sunday-afternoon run is the one that fires, and the
Thu/Fri/Sat runs are silent. Idempotent: notify stamps kv per session.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import notify, voting  # noqa: E402

REMIND_WINDOW_SECS = 26 * 3600   # close is within ~a day → remind
MIN_LEAD_SECS = 30 * 60          # <30min to close → too late to be useful


def main() -> int:
    init_db()
    session = voting.active_session()
    if not session or session.get("status") != "open":
        print("[vote_reminder] no open session")
        return 0
    lead = int(session.get("closes_at") or 0) - int(time.time())
    if not (MIN_LEAD_SECS <= lead <= REMIND_WINDOW_SECS):
        print(f"[vote_reminder] outside window (closes in {lead}s)")
        return 0
    res = notify.send_closing_soon_emails(session)
    print(f"[vote_reminder] sent={len(res['sent'])} printed={len(res['printed'])} "
          f"failed={len(res['failed'])} skipped={res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
