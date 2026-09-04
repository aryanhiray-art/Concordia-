#!/usr/bin/env python3
"""Mirror the latest decided basket into the Alpaca reference account.

Runs after the club's own execution (concord-execute.timer, 10:15 ET) so the
basket it mirrors is the one members actually received that morning.

It executes the basket the way it would be executed for ANY account — the
standard plan: exit names that left the basket, top the rest up to an equal
dollar target. It does not try to reproduce a particular member's holdings;
members differ in stake, entry date and deferred fills, and chasing one of them
would measure that member rather than the strategy.

Placing is still gated: reference.run() checks settings.reference_will_place,
which needs a key pair, REFERENCE_ENABLED, REFERENCE_ARMED and a funded budget.
Disarmed, this job plans and logs exactly what it would have sent.

systemd: concord-reference-run.timer (Mon-Fri 10:45 ET, after concord-execute).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import reference, voting  # noqa: E402

TAG = "[reference_run]"


def main() -> int:
    init_db()
    if not settings.alpaca_configured:
        print(f"{TAG} no Alpaca key pair — nothing to do")
        return 0

    sess = voting.latest_session("executed") or voting.latest_session("closed")
    if not sess:
        print(f"{TAG} no decided session yet — nothing to mirror")
        return 0

    symbols = [p["symbol"] for p in voting.session_picks(sess)]
    if not symbols:
        print(f"{TAG} session {sess['trade_date']} has no picks — nothing to mirror")
        return 0

    out = reference.run(sess["trade_date"], symbols)
    orders = out.get("orders") or []
    placed = [o for o in orders if o.get("status") == "placed"]
    errors = [o for o in orders if o.get("status") == "error"]

    print(f"{TAG} {sess['trade_date']}: {len(orders)} planned, "
          f"{len(placed)} placed, {len(errors)} errored "
          f"(will_place={out.get('will_place')}, paper={out.get('paper')})")
    if out.get("note"):
        print(f"{TAG} note: {out['note']}")
    for o in errors:
        print(f"{TAG} ERROR {o.get('side')} {o.get('symbol')}: {o.get('error')}")

    # A run where every order failed is a failure worth a non-zero exit, so the
    # unit shows up as failed in `systemctl list-units --failed` rather than
    # quietly logging a bad morning.
    return 1 if orders and len(errors) == len(orders) else 0


if __name__ == "__main__":
    raise SystemExit(main())
