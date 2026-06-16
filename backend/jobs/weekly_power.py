#!/usr/bin/env python3
"""Sunday: re-allocate voting power = each connected member's account equity ($).

systemd: concord-weekly-power.timer → this. Records a weekly snapshot + delta
per member so everyone can see their voting power rise/fall week over week.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import power  # noqa: E402


def main() -> int:
    init_db()
    snaps = power.snapshot_all()
    week = power.week_start_et()
    print(f"[weekly_power] week {week}: re-allocated voting power for {len(snaps)} members")
    for s in snaps:
        d = s.get("delta")
        print(f"  member {s['member_id']}: power=${s['power']:.2f}"
              + (f" ({'+' if (d or 0) >= 0 else ''}{d:.2f})" if d is not None else " (first)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
