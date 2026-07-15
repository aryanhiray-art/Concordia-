#!/usr/bin/env python3
"""Reconcile placed executions to REAL broker fills — no orders placed.

Rewrites estimate-based quantities to actual fills so club_shares is truth-based
(the fix for "not all shares get sold"), promotes errored-but-filled orders to
'placed', and demotes broker-rejected 'placed' rows. Safe to run any time; runs
in-process against each member's own vault. systemd schedules it a few minutes
after each execute so the async fills have landed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import reconcile  # noqa: E402


def main() -> int:
    init_db()
    dry = "--dry-run" in sys.argv
    res = reconcile.reconcile_all(dry_run=dry)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
