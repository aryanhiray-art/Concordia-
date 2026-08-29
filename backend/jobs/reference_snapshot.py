#!/usr/bin/env python3
"""Pull the reference account's equity curve + cash flows into reference_nav.

Read-only against Alpaca — this job can never place an order. It exists so the
club's real-money record accrues from first-party data every day instead of
being reconstructed later out of whatever the broker still remembers.

systemd: concord-reference-snapshot.timer (nightly, after the close).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import reference  # noqa: E402


def main() -> int:
    init_db()
    if not settings.alpaca_configured:
        print("[reference_snapshot] no Alpaca key pair — nothing to do")
        return 0
    res = reference.snapshot()
    print(f"[reference_snapshot] {res}")
    if not res.get("ok"):
        return 1
    s = reference.series()
    print(f"[reference_snapshot] {s['n_sessions']} days recorded, "
          f"club={s['club_pct']} spy={s['spy_pct']} since {s['start_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
