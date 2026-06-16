#!/usr/bin/env python3
"""Execute the consensus basket on each opted-in member's account (~09:31 ET).

A REAL place happens only when EXECUTION_MODE=live AND the ARMED flag is on
(see executor.run). Otherwise this records a full review-only plan.

systemd: concord-execute.timer → this. Market-calendar gated.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_trading_day, today_et  # noqa: E402
from app.db import init_db, kv_set  # noqa: E402
from app.services import executor, voting  # noqa: E402


def main() -> int:
    init_db()
    if not is_trading_day(today_et()):
        print("[execute] not a trading day — skipping")
        return 0
    kv_set("halt", "0")
    session = voting.active_session()
    if not session:
        print("[execute] no session")
        return 0
    if session["status"] == "open":
        session = voting.close_and_select(session["id"])  # safety: close first
    summary = executor.run(session, force_dry_run=False)
    print(f"[execute] mode={summary.get('mode')} picks={summary.get('picks')}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
