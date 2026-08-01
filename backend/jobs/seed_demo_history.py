"""Give the DEMO instance a club history to demonstrate with.

The demo is a fresh database, so every club-level view — History's "past
cycles", the latest decided basket, and the decisions-vs-SPY graph the Overview
falls back to — renders an honest but useless empty state. Nobody trying the
demo can see what the product actually does.

This writes plausible PAST cycles (decided baskets + their executions) so those
views have something real to draw. Deterministic, so re-running is idempotent
rather than piling up new fake history.

REFUSES TO RUN OUTSIDE DEMO_MODE. Fabricated cycles in the real club's database
would corrupt the trading record, poison the performance series, and hand
members a track record that never happened — so this checks the flag AND the DB
path before touching anything.

Run from backend/:  python -m jobs.seed_demo_history [--cycles 8]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from app.calendar_us import ET
from app.config import settings
from app.db import db, init_db

# A stable, recognisable spread — big names across sectors so the donut and the
# basket chips look like a real decided basket rather than one industry.
_POOL = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "JPM", "V",
    "UNH", "XOM", "COST", "HD", "PG", "JNJ", "MA", "AVGO",
    "ADBE", "CRM", "KO", "PEP",
]


def _sessions(n: int) -> list[tuple[str, list[str]]]:
    """n past TRADING days, newest last, each with a rotated basket."""
    out: list[tuple[str, list[str]]] = []
    day = datetime.now(ET).date() - timedelta(days=1)
    made = 0
    while made < n:
        if day.weekday() < 5:                      # Mon–Fri only
            # Rotate the pool so consecutive baskets differ but stay stable
            # across runs (no randomness — re-running must not churn history).
            off = made * 3
            basket = [_POOL[(off + i) % len(_POOL)] for i in range(12)]
            out.append((day.isoformat(), basket))
            made += 1
        day -= timedelta(days=1)
    return list(reversed(out))


def seed(cycles: int = 8) -> dict:
    if not settings.demo_mode:
        raise SystemExit("refusing to run: DEMO_MODE is not set — this writes fabricated cycles")
    if "demo" not in (settings.db_path or "").lower():
        raise SystemExit(f"refusing to run: db_path {settings.db_path!r} does not look like a demo database")

    init_db()
    made, skipped = 0, 0
    now = int(time.time())
    with db() as conn:
        member_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM members WHERE email NOT LIKE 'deleted-%'").fetchall()]
        for trade_date, basket in _sessions(cycles):
            existing = conn.execute(
                "SELECT id FROM vote_sessions WHERE trade_date=?", (trade_date,)).fetchone()
            if existing:
                skipped += 1
                continue
            picks = [{"symbol": s, "name": s, "votes": 1, "weight": round(100 / len(basket), 2)}
                     for s in basket]
            closes = int(datetime.fromisoformat(f"{trade_date}T12:30:00").timestamp())
            cur = conn.execute(
                "INSERT INTO vote_sessions(trade_date,status,closes_at,closed_at,top_n,picks_json) "
                "VALUES(?,?,?,?,?,?)",
                (trade_date, "executed", closes, closes, len(basket), json.dumps(picks)),
            )
            sid = cur.lastrowid
            # Executions make the cycle look executed in History's order counts.
            for mid in member_ids[:3]:
                for sym in basket[:6]:
                    conn.execute(
                        "INSERT INTO executions(session_id,member_id,symbol,side,type,"
                        "dollar_amount,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (sid, mid, sym, "buy", "market", 25.0, "placed", closes),
                    )
            made += 1
    return {"created": made, "skipped_existing": skipped, "cycles_requested": cycles}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=8)
    args = ap.parse_args()
    result = seed(args.cycles)
    print(f"[seed_demo_history] {result}", file=sys.stderr)
    print(json.dumps(result))
