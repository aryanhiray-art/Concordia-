#!/usr/bin/env python3
"""Every 10 minutes: warm each member's cached account snapshot from Robinhood.

The member-facing Overview/Account pages read services/accountsnap's
`account_snapshots` row instead of calling Robinhood on every visit (~4s and five
broker round-trips per page load before this existed). This job is what keeps
that row fresh, so a member's page load costs zero live reads.

Members are refreshed SEQUENTIALLY with a gap between them — the point is to stop
hammering the broker, so the job must not itself become a burst. A member whose
refresh fails keeps their previous (good) numbers, flagged not-ok on the row; the
next run retries. NEVER exits non-zero: a Robinhood hiccup must not flap the
timer, and nothing here touches money — it is a display cache.

systemd: concord-account-refresh.timer → this (every 10 min, ET).
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import accountsnap  # noqa: E402

SLEEP_BETWEEN_MEMBERS_S = 1.0


def main() -> int:
    try:
        init_db()
        res = accountsnap.refresh_all_connected(sleep_s=SLEEP_BETWEEN_MEMBERS_S)
        print(f"[refresh_accounts] {res['ok']}/{res['members']} snapshots refreshed "
              f"in {res['seconds']}s"
              + (f" — failed: {res['failed']}" if res["failed"] else ""))
    except Exception:  # noqa: BLE001 — the timer must not flap on a display-cache failure
        traceback.print_exc()
        print("[refresh_accounts] refresh failed — exiting 0 so the timer doesn't flap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
