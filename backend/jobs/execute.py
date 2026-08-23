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

from app.db import init_db  # noqa: E402
from app.services import executor  # noqa: E402


def main() -> int:
    init_db()
    # NOTE: we do NOT clear `halt` here (finding #15). halt is a durable kill
    # switch — clearing it is an explicit owner action (re-arm), never a silent
    # side effect of a scheduled run, so a halt set to cancel a pending
    # liquidation can't be wiped by the next execute.
    # run_today picks the phase scheduled for today: SELL on a sell/vote day
    # (close + exit dropped names), BUY on a buy day (top up the last sold basket),
    # nothing on idle days.
    # trigger="schedule" stamps the run_reports row: this path is the timer.
    summary = executor.run_today(force_dry_run=False, trigger="schedule")
    print(f"[execute] mode={summary.get('mode')} phase={summary.get('phase')} note={summary.get('note')}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
