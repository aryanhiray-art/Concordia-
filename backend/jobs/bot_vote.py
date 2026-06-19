#!/usr/bin/env python3
"""Every trading morning: each AI bot decides + votes in the open session.

systemd: concord-bot-vote.timer → this (run ~07:30 ET, before the 08:30 close).
Idempotent: re-running re-casts each bot's vote. Seeds the 20 bots if missing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_cycle_day, today_et  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import bots, voting  # noqa: E402


def main() -> int:
    init_db()
    seed_universe()
    created = bots.seed_bots()
    if created:
        print(f"[bot_vote] seeded {created} bots")
    if not is_cycle_day(today_et()):
        print("[bot_vote] not a cycle day (cadence) — skipping")
        return 0
    session = voting.ensure_active_session()
    if session["status"] != "open":
        print(f"[bot_vote] no open session (status={session['status']}) — skipping")
        return 0
    res = bots.cast_votes(session)
    print(f"[bot_vote] {res['trade_date']}: voted={res['voted']} "
          f"failed={res['failed']} abstained={res['abstained']}")
    for d in res["details"]:
        if d["status"] == "voted":
            print(f"  {d['bot']}: {','.join(d['picks'])}")
        else:
            print(f"  {d['bot']}: {d['status']}" + (f" — {d.get('err')}" if d.get("err") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
