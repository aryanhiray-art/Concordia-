#!/usr/bin/env python3
"""Open the next trading day's voting session (run the evening before, ET).

systemd: concord-open-vote.timer → this. Market-calendar gated.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import at_et, is_sell_day, next_sell_day, now_et, today_et  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import voting  # noqa: E402


def main() -> int:
    init_db()
    seed_universe()
    td = today_et()
    # Normally the evening-before run opens the NEXT cycle day. But a Persistent
    # catch-up run firing on the MORNING of a sell day (after a missed evening
    # open) must open TODAY's session, not skip ahead to the next cycle — else an
    # outage silently drops a whole vote/rebalance cycle (finding #30).
    if is_sell_day(td) and now_et().timestamp() < at_et(td, settings.vote_close_at).timestamp():
        target = td
    else:
        target = next_sell_day(td)   # cadence-aware next cycle day (not just next trading day)
    session = voting.open_session_for(target)
    print(f"[open_vote] session for {session['trade_date']} status={session['status']} "
          f"closes_at={session['closes_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
