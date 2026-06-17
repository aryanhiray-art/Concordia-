#!/usr/bin/env python3
"""Sunday: re-weight every bot's voting power by last week's pick accuracy.

systemd: concord-bot-reweight.timer → this (run Sunday, alongside weekly_power).
A bot's new power = old power x (1 + realized return of its picks), recorded as a
voting_power_history snapshot + delta — the same shape as members' weekly power.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import bots  # noqa: E402


def main() -> int:
    init_db()
    bots.seed_bots()
    res = bots.score_and_reweight()
    print(f"[bot_reweight] week {res['week']} (scored from {res['scored_from']}):")
    for b in sorted(res["bots"], key=lambda x: x["new_power"], reverse=True):
        r = b["return_pct"]
        print(f"  {b['bot']}: ${b['old_power']:.2f} -> ${b['new_power']:.2f} "
              f"({'+' if (b['delta'] or 0) >= 0 else ''}{b['delta']:.2f}) "
              + (f"[{r:+.2f}% on {b['scored_picks']} picks]" if r is not None else "[no picks]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
