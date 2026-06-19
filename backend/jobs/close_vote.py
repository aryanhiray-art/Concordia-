#!/usr/bin/env python3
"""Lock today's votes and select the top-N consensus picks (run ~09:25 ET).

systemd: concord-close-vote.timer → this. Market-calendar gated.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_sell_day, today_et  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import voting  # noqa: E402


def main() -> int:
    init_db()
    if not is_sell_day(today_et()):
        print("[close_vote] not a cycle day (cadence) — skipping")
        return 0
    td = today_et().isoformat()
    session = voting.session_for_trade_date(td)   # close TODAY's ballot, not the latest
    if not session or session["status"] != "open":
        print(f"[close_vote] no open session for {td} (have {session and session['status']})")
        return 0
    closed = voting.close_and_select(session["id"])
    picks = [p["symbol"] for p in voting.session_picks(closed)]
    print(f"[close_vote] {closed['trade_date']} closed — picks={picks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
