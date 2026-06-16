#!/usr/bin/env python3
"""Open the next trading day's voting session (run the evening before, ET).

systemd: concord-open-vote.timer → this. Market-calendar gated.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import next_trading_day, today_et  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import voting  # noqa: E402


def main() -> int:
    init_db()
    seed_universe()
    target = next_trading_day(today_et())
    session = voting.open_session_for(target)
    print(f"[open_vote] session for {session['trade_date']} status={session['status']} "
          f"closes_at={session['closes_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
