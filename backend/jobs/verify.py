#!/usr/bin/env python3
"""Verify the club's live accounts against its intent — on demand.

Reads each member's REAL Robinhood account (no writes, no orders) and reports
whether everything that should have sold actually sold, surfaces errored orders,
and flags club-ledger drift.

  # after you manually sell everything, confirm the account is flat:
  sudo -u concord /opt/concord/backend/.venv/bin/python jobs/verify.py --flat

  # normal post-cycle check + a gpt-5.5 plain-English report (run as ROOT so
  # OpenClaw can reach the researcher_codex agent):
  /opt/concord/backend/.venv/bin/python jobs/verify.py --narrate

Exit code = 0 on PASS/WARN, 2 on FAIL — so a cron/systemd wrapper can alert.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import verify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flat", action="store_true",
                    help="expect a fully-liquidated account (post manual sell-everything)")
    ap.add_argument("--narrate", action="store_true",
                    help="also print a gpt-5.5 plain-English report (needs OpenClaw creds → run as root)")
    ap.add_argument("--member", type=int, default=None, help="verify only this member id")
    args = ap.parse_args()

    init_db()
    if args.member is not None:
        res = verify.verify_member(args.member, expect_flat=args.flat)
        res = {"verdict": res["verdict"], "members": [res], "report": res["report"]}
    else:
        res = verify.verify_all(expect_flat=args.flat)

    print(res["report"])

    if args.narrate:
        from app.services import narrate as _n
        text = _n.narrate(res)
        print("\n" + "=" * 60)
        if text:
            print("gpt-5.5 report:\n" + text)
        else:
            print("gpt-5.5 report: unavailable (OpenClaw creds/agent not reachable "
                  "for this user — the deterministic report above stands).")

    return 2 if res["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
