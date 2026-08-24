#!/usr/bin/env python3
"""Nightly: aggregate the day's product/ops metrics into `daily_metrics`.

systemd: concord-daily-metrics.timer (23:50 ET) → this. Idempotent — every run
upserts yesterday + today (yesterday again so events landing after last night's
run are folded in), and a first run against an empty table backfills the whole
history from login_events/votes/executions so the admin Metrics charts are
never empty. Safe to rerun by hand any time:

    /opt/concord/backend/.venv/bin/python jobs/daily_metrics.py            # nightly upsert
    /opt/concord/backend/.venv/bin/python jobs/daily_metrics.py --backfill # full rebuild
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import clubnav, metrics  # noqa: E402


def main() -> int:
    init_db()
    if "--backfill" in sys.argv[1:]:
        n = metrics.backfill()
        print(f"[daily_metrics] backfill: rebuilt {n} days")
        return 0
    result = metrics.run_nightly()
    print(f"[daily_metrics] {result['mode']}: wrote {result['days']} day(s)")
    # The club's REAL-money track record is a kept ledger (services/clubnav):
    # extend it every night so the chart reads recorded history rather than a
    # figure re-derived from whatever prices are fetchable at request time.
    nav = clubnav.refresh()
    print(f"[daily_metrics] club_nav: {nav}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
