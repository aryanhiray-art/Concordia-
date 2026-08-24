#!/usr/bin/env python3
"""AI bots vote in the open session — run in RETRYABLE PASSES across the week.

The club trades Monday, but the ballot for a Monday opens the moment the prior
cycle closes, so it is votable for a full week. The bots therefore vote in three
passes and each pass is attempted several times:

    Wed  — first basket
    Sat  — recalculation on fresh prices
    Mon  — final recalculation before the 09:15 ET close

Why passes instead of one Monday-morning run: on 2026-08-24 the single 07:30
run hit the systemd start timeout and was killed with ZERO bots voted, so the
club executed real money on a one-voter tally. Spreading the work means a lost
Monday falls back to Saturday's votes, which fall back to Wednesday's — the
basket is never empty because a job died.

Durability rule (do not break): a pass NEVER clears prior votes up front. Each
bot re-decides and atomically overwrites only its OWN allocations, so a bot that
errors this pass silently keeps the picks it cast last pass.

Resumability: within a pass day, bots that already voted are skipped, so a retry
costs only the bots the killed attempt never reached.

systemd: concord-bot-vote.timer (Wed/Sat/Mon, several attempts each).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import at_et, today_et  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_universe  # noqa: E402
from app.services import bots, voting  # noqa: E402

_PASS_BY_WEEKDAY = {2: "wed", 5: "sat", 0: "mon"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Bots vote in the open session (retryable pass).")
    ap.add_argument("--force", action="store_true",
                    help="re-decide every bot even if it already voted in this pass")
    args = ap.parse_args()

    init_db()
    seed_universe()
    created = bots.seed_bots()
    if created:
        print(f"[bot_vote] seeded {created} bots")

    session = voting.ensure_active_session()
    if session["status"] != "open":
        print(f"[bot_vote] no open session (status={session['status']}) — nothing to do")
        return 0

    td = today_et()
    label = _PASS_BY_WEEKDAY.get(td.weekday(), "adhoc")
    # A pass is one ET calendar day. Every attempt that day shares this boundary,
    # so retries resume; the next pass day rolls the boundary and all bots
    # re-decide on fresh prices.
    cutoff = None if args.force else int(at_et(td, "00:00").timestamp())

    res = bots.cast_votes(session, skip_voted_since=cutoff)
    total = len(bots.bot_members())
    print(f"[bot_vote] pass={label} session={res['trade_date']} "
          f"voted={res['voted']} already={res.get('already', 0)} "
          f"failed={res['failed']} abstained={res['abstained']} of {total}")
    for d in res["details"]:
        if d["status"] == "voted":
            print(f"  {d['bot']}: {','.join(d['picks'])}")
        elif d["status"] != "already_voted":
            print(f"  {d['bot']}: {d['status']}" + (f" — {d.get('err')}" if d.get("err") else ""))

    # Green = this pass is complete. Anything unreached leaves the unit failed so
    # the gap is visible in `systemctl --failed`; the day's next attempt clears it.
    outstanding = res["failed"]
    if outstanding:
        print(f"[bot_vote] {outstanding} bot(s) unreached — a later attempt today will retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
