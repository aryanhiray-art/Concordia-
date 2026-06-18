"""Seed the votable universe from the S&P 500 snapshot. Idempotent.
Run: python -m app.seed
"""
from __future__ import annotations

import time

from .db import db, init_db
from .sp500 import deduped


def seed_universe() -> int:
    init_db()
    rows = deduped()
    with db() as conn:
        for i, (sym, name) in enumerate(rows):
            conn.execute(
                "INSERT INTO universe(symbol,name,sort_order,active) VALUES(?,?,?,1) "
                "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,sort_order=excluded.sort_order,active=1",
                (sym, name, i),
            )
        # Deactivate anything that has since left the index (e.g. RIVN, LCID) so
        # the votable universe — and the research-voter — stay strictly S&P 500.
        syms = [s for s, _ in rows]
        placeholders = ",".join("?" * len(syms))
        conn.execute(f"UPDATE universe SET active=0 WHERE symbol NOT IN ({placeholders})", syms)
        n = conn.execute("SELECT COUNT(*) AS n FROM universe WHERE active=1").fetchone()["n"]
    return n


if __name__ == "__main__":
    print(f"seeded universe: {seed_universe()} symbols")
