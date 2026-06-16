"""Seed the 20-stock voting universe. Idempotent. Run: python -m app.seed"""
from __future__ import annotations

import time

from .db import db, init_db

# 20 liquid, fractional-eligible US names (overlaps the forge watchlists).
UNIVERSE: list[tuple[str, str]] = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet"),
    ("META", "Meta Platforms"),
    ("TSLA", "Tesla"),
    ("AMD", "Advanced Micro Devices"),
    ("NFLX", "Netflix"),
    ("DIS", "Disney"),
    ("HOOD", "Robinhood Markets"),
    ("COIN", "Coinbase"),
    ("PLTR", "Palantir"),
    ("SOFI", "SoFi Technologies"),
    ("UBER", "Uber"),
    ("F", "Ford"),
    ("BAC", "Bank of America"),
    ("KO", "Coca-Cola"),
    ("WMT", "Walmart"),
    ("JPM", "JPMorgan Chase"),
]


def seed_universe() -> int:
    init_db()
    now = int(time.time())
    with db() as conn:
        for i, (sym, name) in enumerate(UNIVERSE):
            conn.execute(
                "INSERT INTO universe(symbol,name,sort_order,active) VALUES(?,?,?,1) "
                "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,sort_order=excluded.sort_order,active=1",
                (sym, name, i),
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM universe WHERE active=1").fetchone()["n"]
    return n


if __name__ == "__main__":
    print(f"seeded universe: {seed_universe()} symbols")
