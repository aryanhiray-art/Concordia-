#!/usr/bin/env python3
"""Every 30 minutes, 06:00–18:30 ET: sweep the unattended-week health checks.

services/watchdog.sweep() checks the conditions a silent week can hit (dead
member tokens, a missed open-vote, quorum/bot risk on a closing ballot, a
halted/disarmed cycle morning, a missed or failed run, stuck sessions,
suspected deposits/withdrawals, disk, stale backups, underfunded stakes) and
alerts ONCE per condition — one batched email per sweep to the admin roster,
plus direct member emails where the fix is the member's own (reconnect, fund).

DB-only apart from the single LLM-probe call inside the bots-idle check.
NEVER exits non-zero: the sweep already fences every check and every email,
and a health-check hiccup must not flap the timer.

systemd: concord-watchdog.timer → this.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import watchdog  # noqa: E402


def main() -> int:
    try:
        init_db()
        res = watchdog.sweep()
        print(f"[watchdog] {res['findings']} active finding(s); "
              f"new: {', '.join(res['new']) if res['new'] else 'none'}"
              + (f"; crashed checks: {', '.join(res['crashed'])}" if res.get("crashed") else ""))
    except Exception:  # noqa: BLE001 — the timer must not flap on a sweep failure
        traceback.print_exc()
        print("[watchdog] sweep failed — exiting 0 so the timer doesn't flap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
