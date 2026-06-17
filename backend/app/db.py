"""SQLite persistence for Concord.

One file DB (`concord.db`, WAL mode) owned by the `concord` unix user. Schema
matches ARCHITECTURE.md §4. A tiny `kv` table holds app flags (ARMED switch,
cached Robinhood client_id). Connections are opened per call; WAL gives us safe
concurrent reads with the single writer (the executor / API).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'member',   -- 'owner' | 'member'
    invite_code_used TEXT,
    password_hash   TEXT,                             -- pbkdf2$iter$salt$hash
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    last_login_at   INTEGER,
    is_bot          INTEGER NOT NULL DEFAULT 0,       -- AI voter (no RH account, never trades)
    bot_strategy    TEXT,                             -- human-readable style label
    bot_slug        TEXT,                             -- stable id for the strategy
    bot_model       TEXT,                             -- ollama model name
    bot_temperature REAL                              -- sampling temperature
);

CREATE TABLE IF NOT EXISTS member_settings (
    member_id        INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    opted_in         INTEGER NOT NULL DEFAULT 0,
    buying_power_pct REAL NOT NULL DEFAULT 0,
    daily_cap_usd    REAL NOT NULL DEFAULT 0,
    max_orders       INTEGER NOT NULL DEFAULT 25,
    voting_power     REAL NOT NULL DEFAULT 0,         -- current weight = allocated $ stake
    alloc_type       TEXT NOT NULL DEFAULT 'percent', -- 'percent' | 'dollar'
    alloc_value      REAL NOT NULL DEFAULT 0,         -- pct of buying power, or fixed USD
    updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS voting_power_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    week_start  TEXT NOT NULL,                        -- YYYY-MM-DD (Sunday, ET)
    power       REAL NOT NULL,                        -- = equity snapshot
    equity      REAL,
    prev_power  REAL,
    delta       REAL,                                 -- power - prev_power
    created_at  INTEGER NOT NULL,
    UNIQUE(member_id, week_start)
);

CREATE TABLE IF NOT EXISTS login_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    ip         TEXT,
    kind       TEXT NOT NULL DEFAULT 'login'          -- 'signup' | 'login'
);

CREATE TABLE IF NOT EXISTS rh_accounts (
    member_id        INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    account_number   TEXT,
    agentic_allowed  INTEGER NOT NULL DEFAULT 0,
    enc_tokens       BLOB,                            -- Fernet(token bundle)
    connected_at     INTEGER,
    last_refresh_at  INTEGER,
    status           TEXT NOT NULL DEFAULT 'connected' -- connected|error|revoked
);

CREATE TABLE IF NOT EXISTS universe (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vote_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT UNIQUE NOT NULL,                  -- YYYY-MM-DD (ET)
    status     TEXT NOT NULL DEFAULT 'open',          -- open|closed|executed
    opened_at  INTEGER,
    closes_at  INTEGER,                               -- epoch of the lock time
    closed_at  INTEGER,
    top_n      INTEGER NOT NULL,
    picks_json TEXT                                   -- set at close
);

CREATE TABLE IF NOT EXISTS votes (
    session_id INTEGER NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    symbol     TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, member_id, symbol)
);

-- A bot's per-session picks with the price at vote time, so we can score the
-- bot's realized return next week (its "accuracy"). Mirrors `votes` but keeps
-- the weight + entry price + thesis the consensus tally doesn't need.
CREATE TABLE IF NOT EXISTS bot_picks (
    session_id  INTEGER NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    weight_pct  REAL NOT NULL DEFAULT 0,              -- bot's own % allocation
    entry_price REAL,                                 -- price when the bot voted
    thesis      TEXT,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (session_id, member_id, symbol)
);
CREATE INDEX IF NOT EXISTS ix_botpicks_member ON bot_picks(member_id);

CREATE TABLE IF NOT EXISTS executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL,
    member_id     INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL DEFAULT 'buy',
    type          TEXT NOT NULL DEFAULT 'market',
    dollar_amount REAL,
    ref_id        TEXT,
    status        TEXT NOT NULL,    -- planned|reviewed|placed|skipped|error
    review_json   TEXT,
    place_json    TEXT,
    error         TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_exec_session ON executions(session_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    detail_json TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to pre-existing tables (deployed DBs predate these fields)."""
    _ALLOWED = {"members", "member_settings"}

    def cols(t: str) -> set[str]:
        assert t in _ALLOWED, f"unexpected table {t!r}"  # PRAGMA can't be parameterized
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
    m = cols("members")
    if "password_hash" not in m:
        conn.execute("ALTER TABLE members ADD COLUMN password_hash TEXT")
    if "last_login_at" not in m:
        conn.execute("ALTER TABLE members ADD COLUMN last_login_at INTEGER")
    for col, ddl in (("is_bot", "is_bot INTEGER NOT NULL DEFAULT 0"),
                     ("bot_strategy", "bot_strategy TEXT"),
                     ("bot_slug", "bot_slug TEXT"),
                     ("bot_model", "bot_model TEXT"),
                     ("bot_temperature", "bot_temperature REAL")):
        if col not in m:
            conn.execute(f"ALTER TABLE members ADD COLUMN {ddl}")
    s = cols("member_settings")
    if "voting_power" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN voting_power REAL NOT NULL DEFAULT 0")
    if "alloc_type" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_type TEXT NOT NULL DEFAULT 'percent'")
    if "alloc_value" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_value REAL NOT NULL DEFAULT 0")


# ─── kv flags ────────────────────────────────────────────────────────────────

def kv_get(key: str, default: str | None = None) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def is_armed() -> bool:
    return kv_get("armed", "false") == "true"


def set_armed(armed: bool, actor: str = "system") -> None:
    kv_set("armed", "true" if armed else "false")
    audit(actor, "arm" if armed else "disarm", "executor", {"armed": armed})


# ─── audit ───────────────────────────────────────────────────────────────────

def audit(actor: str | None, action: str, target: str | None, detail: Any = None) -> None:
    import json
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,detail_json) VALUES(?,?,?,?,?)",
            (int(time.time()), actor, action, target,
             json.dumps(detail) if detail is not None else None),
        )


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
