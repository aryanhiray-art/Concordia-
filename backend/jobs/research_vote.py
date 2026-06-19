#!/usr/bin/env python3
"""Every trading morning: cast the owner's research-voter ballot.

Reads the owner's Robinhood watchlists → keeps S&P 500 names → top 20 →
conviction-weighted allocation → votes. Same path as the forge "Run now" button
and the /api/program/research-vote endpoint. Never moves money — only votes.

systemd: concord-research-vote.timer → this (~07:45 ET, after the bots, before
the 08:30 close). Idempotent: re-running re-casts the owner's allocation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_sell_day, today_et  # noqa: E402
from app.db import init_db  # noqa: E402
from app.routes.deps import owner_member_id  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import research  # noqa: E402


def main() -> int:
    init_db()
    seed_universe()  # keeps the universe (and the S&P 500 cap) current
    if not is_sell_day(today_et()):
        print("[research_vote] not a cycle day (cadence) — skipping")
        return 0
    mid = owner_member_id()
    if mid is None:
        print("[research_vote] no owner member — skipping")
        return 0
    res = research.research_vote(mid, dry=False)
    allocs = res.get("allocations") or []
    print(f"[research_vote] source={res.get('source')} basket={len(allocs)} "
          f"excluded={res.get('excluded')} error={res.get('error')}")
    for a in allocs:
        print(f"  {a['symbol']:<6} {a['weight']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
