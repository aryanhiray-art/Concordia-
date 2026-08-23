"""Persist one `run_reports` row per executor invocation.

Executor run summaries used to be PRINTED to journald and returned to the
caller — never stored — so execution forensics meant ssh + journalctl (the
08/19 deferred-buys incident was debugged exactly that way). This module makes
every run (idle/no-op runs included) a queryable DB row: the light columns
drive the admin Runs list, `summary_json` holds the FULL summary dict for the
per-run drilldown.

Hard rule: report persistence must NEVER fail a run — the caller may have just
placed real orders, and losing the report is strictly better than raising after
money moved. `persist()` therefore swallows every exception.
"""
from __future__ import annotations

import json
import time

from ..db import db

# Per-order statuses that roll up into the list columns. `reviewed`/`held`
# orders are visible in summary_json but aren't counted — the columns answer
# "what did this run actually do / fail to do".
_COUNTED = ("placed", "skipped", "error")


def persist(summary: dict, *, trigger: str, started_at: int,
            finished_at: int | None = None) -> None:
    """Insert the run's report row. Swallows every failure (see module doc)."""
    try:
        counts = dict.fromkeys(_COUNTED, 0)
        for o in (summary.get("orders") or []):
            st = o.get("status") if isinstance(o, dict) else None
            if st in counts:
                counts[st] += 1
        verification = summary.get("verification") or {}
        verdict = verification.get("verdict") if isinstance(verification, dict) else None
        with db() as conn:
            conn.execute(
                'INSERT INTO run_reports(trade_date,phase,mode,"trigger",will_place,'
                "started_at,finished_at,placed,skipped,errors,verify_verdict,summary_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (summary.get("trade_date"), summary.get("phase"), summary.get("mode"),
                 trigger, 1 if summary.get("will_place") else 0,
                 int(started_at), int(finished_at if finished_at is not None else time.time()),
                 counts["placed"], counts["skipped"], counts["error"],
                 verdict, json.dumps(summary, default=str)),
            )
    except Exception:  # noqa: BLE001 — never fail a run over its report
        pass
