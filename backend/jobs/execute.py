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

from app.db import init_db, kv_set  # noqa: E402
from app.services import executor  # noqa: E402


def main() -> int:
    init_db()
    kv_set("halt", "0")
    # run_today picks the phase scheduled for today: SELL on a sell/vote day
    # (close + exit dropped names), BUY on a buy day (top up the last sold basket),
    # nothing on idle days.
    summary = executor.run_today(force_dry_run=False)
    print(f"[execute] mode={summary.get('mode')} phase={summary.get('phase')} note={summary.get('note')}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
