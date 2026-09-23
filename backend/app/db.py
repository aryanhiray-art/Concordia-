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
    bot_temperature REAL,                             -- sampling temperature
    email_verified_at INTEGER                         -- inbox control proven (code / Google); NULL = never
);

CREATE TABLE IF NOT EXISTS member_settings (
    member_id        INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    opted_in         INTEGER NOT NULL DEFAULT 0,
    buying_power_pct REAL NOT NULL DEFAULT 0,         -- deprecated, unread
    daily_cap_usd    REAL NOT NULL DEFAULT 0,         -- 0 = uncapped (see alloc >0 convention)
    max_orders       INTEGER NOT NULL DEFAULT 25,     -- 0 = global default (settings.max_orders)
    voting_power     REAL NOT NULL DEFAULT 0,         -- current weight (blended when POWER_MODEL=blend)
    alloc_type       TEXT NOT NULL DEFAULT 'percent', -- 'percent' | 'dollar'
    alloc_value      REAL NOT NULL DEFAULT 0,         -- pct of buying power, or fixed USD
    accuracy            REAL NOT NULL DEFAULT 0,      -- blended (short+long) EWMA excess return vs benchmark (signed)
    accuracy_short      REAL NOT NULL DEFAULT 0,      -- fast EWMA component ("current form")
    accuracy_long       REAL NOT NULL DEFAULT 0,      -- slow EWMA component ("career record")
    cycles_participated INTEGER NOT NULL DEFAULT 0,   -- scored cycles → confidence ramp
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

-- KEPT club record: one row per ET day of the REAL-money club index (see
-- services/clubnav). This is a ledger, not a cache — the club's track record
-- must not be re-derived from whatever prices happen to be fetchable later.
-- The REFERENCE account's daily record (one operator-owned Alpaca account that
-- mirrors the club's decided basket). Stores RAW OBSERVATIONS, never a computed
-- index: clubnav stored the index itself, so when the method changed the whole
-- published history silently moved (+2.05% -> +9.76% on 2026-08-25 because one
-- member's allocation row was edited). Keeping equity and the real cash flow
-- means the series can be recomputed at read time and a method fix never
-- rewrites what was actually observed.
CREATE TABLE IF NOT EXISTS reference_nav (
    d          TEXT PRIMARY KEY,              -- ET date, YYYY-MM-DD
    equity     REAL NOT NULL,                 -- account equity at that day's close
    net_flow   REAL NOT NULL DEFAULT 0,       -- deposits(+)/withdrawals(-) that day, from
                                              -- the activities feed — a REAL number, not
                                              -- clubnav's >25%-jump heuristic
    spy_close  REAL,                          -- benchmark close (NULL = unfetched)
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS club_nav (
    d          TEXT PRIMARY KEY,              -- ET date, YYYY-MM-DD
    club       REAL NOT NULL,                 -- index, 100 = first recorded day
    spy        REAL,                          -- benchmark index, same base (NULL = unfetched)
    members    INTEGER NOT NULL DEFAULT 0,    -- member-days that fed this day's return
    created_at INTEGER NOT NULL
);

-- Minted invite codes (alongside the legacy shared INVITE_CODE env string).
-- A code admits while: not revoked, not expired, uses < max_uses (NULL = ∞).
CREATE TABLE IF NOT EXISTS invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,
    note       TEXT,                                  -- who/what this code is for
    created_by INTEGER REFERENCES members(id),
    created_at INTEGER NOT NULL,
    expires_at INTEGER,                               -- epoch seconds; NULL = never
    max_uses   INTEGER,                               -- NULL = unlimited
    uses       INTEGER NOT NULL DEFAULT 0,
    revoked_at INTEGER
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

-- ── vote_sessions.status — the cycle's own state machine ────────────────────
-- open      voting.open_session INSERTs it. Members may vote until closes_at.
--           An 'open' row whose closes_at is more than STALE_OPEN_AFTER_SECS
--           (1 day) past was ABANDONED mid-cycle — voting.is_abandoned_open is
--           the single source of that rule, and the views present such a row as
--           'expired'. It is cruft, not a live ballot, and is never mutated.
-- closed    voting.close_session: votes locked, picks_json + cycle_prices
--           written. The executor's buy leg only ever processes the LATEST
--           closed session, so an older row stuck here can never trade again.
-- executed  executor, after the BUY phase completes — and ONLY when zero 'error'
--           executions remain, no member was stranded mid-pass, and the run was
--           not halted. Flipping early would hide the session from the next
--           run's reconcile/A4 pass. Terminal.
-- lapsed    executor._retire_stale_sessions: a 'closed' session older than
--           STALE_BASKET_DAYS (5) that the buy leg now refuses. Its remaining
--           'error' executions become 'abandoned' in the same sweep. Terminal.
--           Internal bookkeeping only — members see it as an ordinary past cycle.
--
-- TERMINAL: executed, lapsed — and reconcile._session_retired treats exactly
-- those two (or a trade_date more than 5 days old) as "this session can no
-- longer re-place anything", which is the gate on returning an errored buy's
-- dollars to the member's carry.
CREATE TABLE IF NOT EXISTS vote_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT UNIQUE NOT NULL,                  -- YYYY-MM-DD (ET)
    status     TEXT NOT NULL DEFAULT 'open',          -- open|closed|executed|lapsed
                                                      -- (state machine above)
    opened_at  INTEGER,
    closes_at  INTEGER,                               -- epoch of the lock time
    closed_at  INTEGER,
    top_n      INTEGER NOT NULL,
    picks_json TEXT,                                  -- set at close
    scored_at  INTEGER                                -- when reputation scored this cycle's picks (idempotency)
);

CREATE TABLE IF NOT EXISTS votes (
    session_id INTEGER NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    symbol     TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 100,    -- % of this member's voting power on this pick
    created_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, member_id, symbol)
);

-- Entry price of every VOTED symbol (+ the benchmark) captured at session close,
-- so a member's vote-weighted return vs the benchmark can be scored a cycle later
-- (accuracy = paper basis on what you backed, even picks that lost the vote).
CREATE TABLE IF NOT EXISTS cycle_prices (
    session_id  INTEGER NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,                        -- ticker, or the benchmark symbol
    price       REAL NOT NULL,
    captured_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, symbol)
);

-- Append-only per-cycle scoring ledger. NOT read by the formula (the live value
-- stays member_settings.accuracy) — it exists so "rescale, never reset" is
-- AUDITABLE: every accuracy change is attributable to one cycle, with the exact
-- alphas and roster size that produced it. It also makes any future formula
-- change replayable from history instead of un-derivable, and records the
-- per-cycle dispersion needed to decide later whether cross-sectional
-- standardisation is warranted (see reputation.py).
CREATE TABLE IF NOT EXISTS cycle_scores (
    session_id   INTEGER NOT NULL REFERENCES vote_sessions(id) ON DELETE CASCADE,
    member_id    INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    excess       REAL NOT NULL,        -- winsorised, the value that entered the EWMA
    excess_raw   REAL NOT NULL,        -- pre-winsorisation, so clipping is visible
    alpha_short  REAL NOT NULL,        -- the membership-scaled alphas actually applied
    alpha_long   REAL NOT NULL,
    n_measured   INTEGER NOT NULL,     -- members with a priced pick this cycle
    n_members    INTEGER,              -- ACTIVE member count: the R1 input, stamped and never re-read
    picks_scored INTEGER NOT NULL,
    dispersion   REAL,                 -- stdev of this cycle's excess across members
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (session_id, member_id)
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

-- ── executions.status — THE STATE MACHINE ───────────────────────────────────
-- Read this during an incident BEFORE touching a row by hand. Two facts drive
-- every decision below:
--   • positions.club_shares counts 'placed' AND NOTHING ELSE. A row in any other
--     status contributes zero shares to the club ledger.
--   • reconcile.reconcile_member's candidate set is 'placed' + 'error' + a
--     'skipped' row whose place_json carries _concord_awaiting_credit (a parked
--     carry claim). Rows with _concord_aged_out set are never re-scanned.
-- So "will the next reconcile pass still look at this row?" and "does this row
-- own shares?" are two different questions with two different answers.
--
-- planned   VESTIGIAL. Declared here, but NO code path writes it — verified
--           across backend/ (2026-09-23). It survives only as a DELETE target in
--           executor._clear_replanable. Treat a 'planned' row in prod as data
--           from a dead code path, not as a live state.
-- reviewed  executor._record on a review-only pass: the pre-trade review came
--           back clean but will_place was False (dry run / not armed). NOTHING
--           reached the broker. Non-terminal: _clear_replanable DELETES it on the
--           next run of the same session. Not counted, not a reconcile candidate.
-- placed    The broker accepted an order. Written by executor._record after a
--           successful place, by rescue.py (operator rescue sell), by renames.py
--           (synthetic CORPACT row, no broker order behind it) and by reconcile
--           when it promotes an 'error' row it proved filled. The ONLY counted
--           status, and _clear_replanable never deletes it (_placed_keys also
--           blocks re-placing the same member/symbol/side).
--           NOT STRICTLY TERMINAL — this is the one that surprises operators:
--           reconcile DEMOTES a 'placed' row to 'skipped' when the broker says
--           the order was rejected/cancelled with a zero fill, so club_shares
--           stops claiming shares that never existed.
-- skipped   Nothing reached the broker, and that is settled: no quote, max_orders
--           cap, blocking pre-trade alert, review call raised, auth died before
--           the place, unsellable residue, unknown basket. Also reconcile's
--           landing state for a demoted 'placed' row and for an aged-out 'error'
--           row. Not counted. _clear_replanable DELETES it on a re-run, which is
--           deliberate — deleting the row also discards its parked carry claim,
--           and the re-run re-places the order instead.
-- error     The place call itself RAISED, so the broker MAY have accepted the
--           order. This is evidence, not a failure record. Non-terminal, and it
--           deliberately SURVIVES _clear_replanable so a re-run can never blindly
--           re-place an order that may already exist. It also holds the session
--           open: the flip to vote_sessions.status='executed' requires zero
--           remaining 'error' rows. Exits only by positive resolution —
--             → placed      reconcile matched a real fill (promotion)
--             → skipped     reconcile proved it dead at the broker, or aged it
--                           out after _ERROR_AGE_OUT_S (3d) when the order feed
--                           provably reaches back past the row; an aged-out BUY
--                           credits its dollars to carry_pending exactly once
--             → superseded  executor is about to re-place this exact key, or has
--                           carried the budget forward, or verified the order
--                           absent from the broker's own recent-order feed
--             → abandoned   the session aged out from under it
-- superseded TERMINAL. Executor-only: _supersede_error (immediately before a new
--           order for the same member/symbol/side is placed, so the stale row can
--           never be fuzzy-matched to the NEW order and double-count the ledger),
--           the carried-forward-budget path, and the A4 verified-absent
--           retirement. Not counted, not a reconcile candidate, and
--           _clear_replanable garbage-collects it. Because it is invisible to
--           both, retiring on an UNPROVEN absence would make a real fill
--           permanently invisible — hence the executor only writes it on proof.
-- abandoned TERMINAL. Written in exactly one place: executor._retire_stale_sessions
--           flips a stale session's remaining 'error' rows when a 'closed' session
--           ages past STALE_BASKET_DAYS (5). The error text is preserved. It does
--           NOT return an abandoned buy's dollars to the member's slice — only
--           reconcile reads the broker, so only reconcile can prove an order never
--           landed, and crediting one that DID land would spend it twice. Not
--           counted, not a reconcile candidate, and never deleted.
--
-- TERMINAL: superseded, abandoned. Everything else can still move — including
-- 'placed', via reconcile's demotion path.
CREATE TABLE IF NOT EXISTS executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL,
    member_id     INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL DEFAULT 'buy',
    type          TEXT NOT NULL DEFAULT 'market',
    dollar_amount REAL,
    ref_id        TEXT,
    status        TEXT NOT NULL,    -- planned|reviewed|placed|skipped|error|
                                    -- superseded|abandoned — state machine above
    review_json   TEXT,
    place_json    TEXT,
    error         TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_exec_session ON executions(session_id);
CREATE INDEX IF NOT EXISTS ix_exec_member ON executions(member_id);
CREATE INDEX IF NOT EXISTS ix_exec_ref ON executions(ref_id);

-- One row per EXECUTOR INVOCATION — idle/no-op runs included (phase may be
-- NULL/'none'; the absence of a record is itself a gap). Execution forensics
-- become a DB read instead of ssh + journalctl: summary_json is the full run
-- summary dict (per-member orders, buying power, notes, verification).
-- "trigger" is quoted because TRIGGER is an SQLite keyword; the column name
-- is exactly `trigger` ('schedule' | 'manual:<email>').
CREATE TABLE IF NOT EXISTS run_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT,
    phase          TEXT,
    mode           TEXT,
    "trigger"      TEXT NOT NULL,
    will_place     INTEGER NOT NULL DEFAULT 0,
    started_at     INTEGER NOT NULL,
    finished_at    INTEGER NOT NULL,
    placed         INTEGER,
    skipped        INTEGER,
    errors         INTEGER,
    verify_verdict TEXT,
    summary_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    detail_json TEXT
);

-- Personal API keys: let a member connect an external program (e.g. the
-- research-voter) that votes on their behalf. Only the sha256 hash is stored;
-- the full key (ck_live_...) is shown once at creation.
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    name         TEXT NOT NULL DEFAULT 'API key',
    prefix       TEXT NOT NULL,             -- first chars (ck_live_xxxx), shown in UI
    key_hash     TEXT NOT NULL,             -- sha256 hex of the full key
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER,
    revoked_at   INTEGER
);
CREATE INDEX IF NOT EXISTS ix_apikeys_member ON api_keys(member_id);
CREATE INDEX IF NOT EXISTS ix_apikeys_prefix ON api_keys(prefix);

-- Recorded acceptances of the club agreement (and any future consent docs).
-- Append-only: one row per acceptance, kind identifies the document.
CREATE TABLE IF NOT EXISTS consents (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,                -- e.g. 'club_agreement_v1'
    version   TEXT,
    at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_consents_member ON consents(member_id);

-- Short-lived email verification codes (signup / password reset). Only the
-- sha256 of the 6-digit code is stored; one active row per (email, purpose) —
-- issuing a new code replaces the old. attempts caps brute force at 5.
CREATE TABLE IF NOT EXISTS email_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    purpose    TEXT NOT NULL,                -- 'signup' | 'reset'
    code_hash  TEXT NOT NULL,                -- sha256 hex of the 6-digit code
    expires_at INTEGER NOT NULL,             -- epoch; 15 min after issue
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_emailcodes_email ON email_codes(email, purpose);

-- External sign-in identities (Google). `subject` is the provider's stable
-- user id (the ID-token `sub`), unique across the club; email is a snapshot.
CREATE TABLE IF NOT EXISTS oauth_identities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    provider   TEXT NOT NULL,               -- 'google'
    subject    TEXT UNIQUE,
    email      TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oauth_member ON oauth_identities(member_id);

-- DISPLAY cache of a member's Robinhood-backed account view, refreshed on an
-- interval (jobs/refresh_accounts.py) so the Overview/Account pages are instant
-- DB reads instead of ~4s of live broker calls on every single visit.
-- Written ONLY by services/accountsnap.py, read ONLY by the /api/me read routes.
-- The TRADING path must never read this table — the executor, voting power and
-- reputation always take a live balance (sizing real orders off a stale number
-- could over-order real money).
CREATE TABLE IF NOT EXISTS account_snapshots (
    member_id    INTEGER PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    payload      TEXT NOT NULL,               -- JSON {allocation, pnl, power}
    holdings     TEXT NOT NULL,               -- JSON [{symbol, quantity, value, pnl}]
    refreshed_at INTEGER NOT NULL,            -- epoch of the last SUCCESSFUL broker read
    ok           INTEGER NOT NULL DEFAULT 1,  -- 0 = last refresh failed; payload is the last GOOD one
    error        TEXT                         -- why the last refresh failed (truncated)
);

-- RECORDED account-value history: one row per real observation of a member's
-- total account value. Written by services/equity_history on every SUCCESSFUL
-- snapshot refresh (~every 10 min), pruned by the same module.
--
-- Why a table at all: the only account-value history the club had was
-- voting_power_history.equity — written ONCE A WEEK — so the member chart was
-- ~6 real dots with straight lines invented between them. Robinhood exposes no
-- portfolio-history endpoint (only equity/option/index historicals), so this
-- CANNOT be backfilled from the broker: it has to be recorded going forward.
-- Every row here is an observed number; nothing in this table is interpolated.
CREATE TABLE IF NOT EXISTS equity_points (
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    ts          INTEGER NOT NULL,             -- epoch seconds of the observation
    total_value REAL NOT NULL                 -- $ total account value as read
);
-- UNIQUE (not just indexed): two writers landing in the same second must
-- collapse to one row, which is also what makes the weekly-history seed
-- idempotent (INSERT OR IGNORE) and the retention prune exact.
CREATE UNIQUE INDEX IF NOT EXISTS ix_equity_points_member_ts
    ON equity_points(member_id, ts);

-- Suspected member cash flows (deposits/withdrawals), recorded by the watchdog
-- (W9): an equity step >2% with no club executions in the surrounding window.
-- Detection only for now — return math stays flow-naive; this table is the
-- foundation for deposit-aware returns. UNIQUE(member_id, ts) makes the
-- once-per-step dedupe an INSERT OR IGNORE.
CREATE TABLE IF NOT EXISTS member_flows (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    ts        INTEGER NOT NULL,             -- epoch of the equity point AFTER the step
    amount    REAL,                         -- signed $ (deposit > 0, withdrawal < 0)
    kind      TEXT NOT NULL DEFAULT 'suspected',
    source    TEXT NOT NULL DEFAULT 'auto',
    note      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_member_flows_member_ts
    ON member_flows(member_id, ts);

-- TRACKING LAYER: one row per ET calendar day of product/ops aggregates,
-- written by the nightly job (jobs/daily_metrics.py → services/metrics.py).
-- Pure derivations over rows other tables already hold (members, login_events,
-- votes, vote_sessions, executions, voting_power_history) — nothing here is a
-- source of truth, so the whole table can always be rebuilt via the job's
-- backfill mode. Upserts are idempotent: rerunning a day replaces the row.
-- NULL-able columns mean "not applicable / unknown that day" (no ballot, no
-- waitlist table), never zero.
CREATE TABLE IF NOT EXISTS daily_metrics (
    date                  TEXT PRIMARY KEY,           -- YYYY-MM-DD (ET)
    member_count          INTEGER NOT NULL DEFAULT 0, -- non-bot, non-deleted, signed up by EOD
    activation_count      INTEGER NOT NULL DEFAULT 0, -- connected + opted in + stake set
    dau                   INTEGER NOT NULL DEFAULT 0, -- distinct humans who logged in or voted
    wau                   INTEGER NOT NULL DEFAULT 0, -- same, trailing 7 days ending this day
    voters                INTEGER,                    -- human voters on this day's ballot
    eligible              INTEGER,                    -- humans who could have voted
    ballot_participation  REAL,                       -- voters/eligible; NULL on no-ballot days
    committed_capital_usd REAL NOT NULL DEFAULT 0,    -- latest weekly equity sum as of this day
    executed_notional_usd REAL NOT NULL DEFAULT 0,    -- $ placed by the executor this day
    error_count           INTEGER NOT NULL DEFAULT 0, -- execution errors this day
    waitlist_count        INTEGER,                    -- live count; NULL only on rows computed before the table existed
    computed_at           INTEGER NOT NULL            -- epoch of the last (re)computation
);

-- Addresses left on the PUBLIC marketing site (POST /api/public/waitlist).
-- Deliberately thin, and deliberately NOT members: a row here grants nothing,
-- is never emailed by the endpoint that writes it, and converts only when the
-- owner sends an invite by hand. UNIQUE(email) is what lets that unauthenticated
-- route answer identically whether or not the address is already known — the
-- insert is an INSERT OR IGNORE, so a duplicate submit cannot be used to probe
-- who is on the list.
CREATE TABLE IF NOT EXISTS waitlist (
    id         INTEGER PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    source     TEXT,                         -- which block of the page it came from
    created_at INTEGER NOT NULL
);

-- The nightly aggregation (and its first-run backfill) scans events by
-- timestamp; these covering indexes keep it a range read, not a table scan.
CREATE INDEX IF NOT EXISTS ix_login_events_ts ON login_events(ts);
CREATE INDEX IF NOT EXISTS ix_exec_created ON executions(created_at);
CREATE INDEX IF NOT EXISTS ix_votes_created ON votes(created_at);

-- THE CLUB FEED (routes/club.py): member posts and replies, one table.
-- parent_id NULL = top-level post; otherwise a flat reply to that post (no
-- deeper nesting on purpose — Reddit mechanics, not Reddit topology).
-- Rows are soft-deleted (deleted=1, body scrubbed to '') so a removed post
-- keeps its replies' thread shape; a member's FULL account deletion hard-
-- deletes their rows instead (routes/members.py delete_account — feed posts
-- are personal content and join that contract). Bodies are sanitized and
-- profanity-checked server-side (services/textguard.py) before insert.
CREATE TABLE IF NOT EXISTS club_posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL,
    parent_id  INTEGER,                       -- NULL = top-level
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_club_posts_parent  ON club_posts(parent_id);
CREATE INDEX IF NOT EXISTS ix_club_posts_member  ON club_posts(member_id);
CREATE INDEX IF NOT EXISTS ix_club_posts_created ON club_posts(created_at);

-- One vote per member per post, direction +1/-1; changing your mind is an
-- upsert, withdrawing is a delete (routes/social.py). Votes are feed karma
-- ONLY: nothing here may ever feed back into reputation/voting power — that
-- direction is the load-bearing invariant of the merit form (reputation.py
-- header), and the feed reads power, never writes anything power reads.
CREATE TABLE IF NOT EXISTS club_post_votes (
    post_id    INTEGER NOT NULL,
    member_id  INTEGER NOT NULL,
    dir        INTEGER NOT NULL,              -- +1 or -1
    created_at INTEGER NOT NULL,
    PRIMARY KEY (post_id, member_id)
);
CREATE INDEX IF NOT EXISTS ix_club_post_votes_member ON club_post_votes(member_id);
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
    # Voting-power knobs are validated HERE, not only in main.startup, because
    # the API is 1 of ~17 entrypoints: every unit in backend/jobs/ is its own
    # process calling init_db() directly. A refusing API does not stop
    # concord-weekly-power, concord-close-vote or concord-execute, so a bad knob
    # (say CAPITAL_GAMMA_BPS with one extra zero, which AMPLIFIES large stakes)
    # would take the website down loudly while the timers quietly re-weighted
    # the club and traded the result in members' live accounts.
    from .config import invalid_power_config
    bad = invalid_power_config()
    if bad:
        raise RuntimeError("Refusing to start: invalid voting-power configuration — "
                           + "; ".join(bad))
    with db() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
    # Defence-in-depth (finding #32): the DB holds the Fernet-encrypted OAuth
    # vault + hashed credentials — make it (and its WAL/SHM sidecars) owner-only
    # rather than trusting the parent directory's mode alone.
    try:
        import os
        for _suffix in ("", "-wal", "-shm"):
            _p = settings.db_path + _suffix
            if os.path.exists(_p):
                os.chmod(_p, 0o600)
    except OSError:
        pass


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to pre-existing tables (deployed DBs predate these fields)."""
    _ALLOWED = {"members", "member_settings", "votes", "vote_sessions",
                "voting_power_history", "cycle_scores"}

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
    if "email_verified_at" not in m:
        conn.execute("ALTER TABLE members ADD COLUMN email_verified_at INTEGER")
    # Membership lifecycle. Until v2 there was NONE — no left_at, no is_active —
    # so the only departure signal was self-service deletion anonymizing the row
    # to deleted-N@deleted.invalid. adminview/metrics/watchdog filtered on that
    # email pattern; reputation.snapshot_power_all and voting._tally_rows did
    # not, so a departed member could still sit in the voting-power denominator
    # and still land a ticker in the basket. NULL = active.
    if "left_at" not in m:
        conn.execute("ALTER TABLE members ADD COLUMN left_at INTEGER")
        # Backfill: rows already anonymized by self-service deletion are departed
        # by definition. Idempotent (guarded on left_at IS NULL), and scoped by
        # the same email fingerprint the other services already trust.
        conn.execute(
            "UPDATE members SET left_at = COALESCE("
            "  (SELECT s.updated_at FROM member_settings s WHERE s.member_id = members.id),"
            "  CAST(strftime('%s','now') AS INTEGER)) "
            "WHERE email LIKE 'deleted-%@deleted.invalid' AND left_at IS NULL")
    if "weight" not in cols("votes"):
        conn.execute("ALTER TABLE votes ADD COLUMN weight REAL NOT NULL DEFAULT 100")
    # Optional per-pick rationale — the "why" a member attaches to a vote.
    if "thesis" not in cols("votes"):
        conn.execute("ALTER TABLE votes ADD COLUMN thesis TEXT")
    s = cols("member_settings")
    if "voting_power" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN voting_power REAL NOT NULL DEFAULT 0")
    if "alloc_type" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_type TEXT NOT NULL DEFAULT 'percent'")
    if "alloc_value" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_value REAL NOT NULL DEFAULT 0")
    # The stake as the member SET it. alloc_type/alloc_value are the executor's
    # OPERATIONAL figures: with compounding on, every deployed cycle rewrites
    # them to 'dollar' + the deployed amount so future edits apply as deltas —
    # which also silently erased the fact that a member chose "100%". A frozen
    # $111.74 then drifted $0.06 under the live balance and the watchdog told a
    # full-stake member to "add funds" over six cents. Intent is written ONLY by
    # the member's own save (routes/members.py PUT /me/allocation); the executor
    # never touches it. NULL = the stake was last saved before these columns
    # existed, i.e. intent unknown.
    if "alloc_intent_type" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_intent_type TEXT")
    if "alloc_intent_value" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_intent_value REAL")
    # Reputation (blended voting power): per-member EWMA accuracy + confidence ramp.
    if "accuracy" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN accuracy REAL NOT NULL DEFAULT 0")
    if "cycles_participated" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN cycles_participated INTEGER NOT NULL DEFAULT 0")
    # Two-timescale accuracy (short = current form, long = career record) — see
    # config.accuracy_short_half_life_cycles / accuracy_long_half_life_cycles.
    # `accuracy` remains the blended column every downstream reader already uses.
    if "accuracy_short" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN accuracy_short REAL NOT NULL DEFAULT 0")
    if "accuracy_long" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN accuracy_long REAL NOT NULL DEFAULT 0")
    # SEED both components from the accumulated single-EWMA score. Without this
    # the next score_cycle blends from 0 — new = a_short*this + 0 — which
    # collapses every member's hard-won accuracy to a ONE-CYCLE value while
    # cycles_participated (and therefore confidence) stays maxed, i.e. full
    # weight on a score that no longer carries any history. Seeding both to
    # `accuracy` is exactly continuous: w_short*acc + w_long*acc == acc.
    # The WHERE clause is a precise fingerprint of the un-seeded state — after
    # any scoring run, accuracy is DERIVED from the two components, so
    # (accuracy != 0 AND both components 0) is unreachable. Idempotent.
    conn.execute("UPDATE member_settings SET accuracy_short=accuracy, accuracy_long=accuracy "
                 "WHERE accuracy != 0 AND accuracy_short = 0 AND accuracy_long = 0")
    # Last SUCCESSFULLY read committed capital. Used only by the held-member path:
    # when a CONNECTED member's balance read fails transiently, they keep this
    # value in the denominator instead of being dropped from it — dropping them
    # inflates every survivor's share by 1/(1-share_held) while the held member's
    # stale dollar power keeps voting.
    if "last_capital_usd" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN last_capital_usd REAL NOT NULL DEFAULT 0")
        # SEED from the last snapshotted power. Without this the column is 0 for
        # everyone on the first run, so the held-member carry has nothing to
        # carry and a single transient broker error drops that member out of the
        # denominator — on a real roster that hands the remaining members a
        # wildly inflated share of a live-money vote. Idempotent.
        # Only under POWER_MODEL=stake, where voting_power IS the committed
        # stake. Under blend it is BLENDED power, and seeding that would let the
        # held-member carry put a blended number into the capital pool on the
        # first post-cutover run (measured: $2,027 carried against $1,000 of real
        # capital). It self-corrects on the first successful read, but the
        # cutover is exactly when it must not happen.
        if settings.power_model != "blend":
            conn.execute("UPDATE member_settings SET last_capital_usd = voting_power "
                         "WHERE last_capital_usd = 0 AND voting_power > 0")
    # The CANONICAL voting weight is the unrounded share; member_settings.voting_power
    # keeps holding DOLLARS (voting._tally_rows and every web money() call site
    # read it, and voting.py floors it at 1.0 — a 0..1 share would put every
    # member under that floor and silently collapse the club to one-member-one-vote).
    if "voting_power_share" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN voting_power_share REAL NOT NULL DEFAULT 0")
    # History rows gain the share + the club context needed to render a snapshot
    # HONESTLY. Today the member chart shows only a dollar delta, so when someone
    # else leaves or deposits, every remaining member sees a personal "loss" they
    # did not cause. share/pool_usd/n_active separate "my standing changed" from
    # "the pool changed". All nullable and additive — old rows stay readable.
    if "n_members" not in cols("cycle_scores"):
        conn.execute("ALTER TABLE cycle_scores ADD COLUMN n_members INTEGER")
    v = cols("voting_power_history")
    for col, ddl in (("share", "share REAL"),
                     ("pool_usd", "pool_usd REAL"),
                     ("n_active", "n_active INTEGER"),
                     ("rank_at_snapshot", "rank_at_snapshot INTEGER")):
        if col not in v:
            conn.execute(f"ALTER TABLE voting_power_history ADD COLUMN {ddl}")
    # Ballot lifecycle emails (open / closing-soon). Default ON: a weekly-cadence
    # club lives or dies on members remembering the ballot exists.
    if "email_reminders" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN email_reminders INTEGER NOT NULL DEFAULT 1")
    # Compounding stake (owner decision 2026-08-24, default ON): a dollar stake
    # rides its own P&L in BOTH directions from the committed base instead of
    # being pinned; alloc_applied = the last stake figure the executor honored,
    # so a stake CHANGE deploys/retires exactly the delta.
    if "compound" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN compound INTEGER NOT NULL DEFAULT 1")
    if "alloc_applied" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN alloc_applied REAL")
        # Backfill: an existing member's CURRENT stake is already deployed, so
        # it counts as honored. Without this, a stake change made between the
        # migration and that member's first compounding run would be silently
        # absorbed (NULL reads as "already applied").
        conn.execute("UPDATE member_settings SET alloc_applied=alloc_value "
                     "WHERE alloc_applied IS NULL AND alloc_value > 0")
    # Club dollars the last cycle meant to deploy but could not (deferred for
    # settled cash, order-cap). Carried into the next budget so undeployed cash
    # cannot silently leave the slice; recomputed fresh each buy leg.
    if "slice_carry" not in s:
        conn.execute("ALTER TABLE member_settings ADD COLUMN slice_carry REAL NOT NULL DEFAULT 0")
    # Club dollars proven (at the broker) never to have been deployed by an
    # EARLIER cycle — an errored order reconcile has since verified absent or
    # dead. They arrive asynchronously, so they cannot live in slice_carry,
    # which the buy leg's write-back overwrites wholesale every cycle; they
    # accumulate here and the next write-back drains them into the slice in one
    # transaction. reconcile is the only writer, the write-back is the only
    # drainer, and every credit is marked on its execution row — so a dollar can
    # be credited once and deployed once.
    if "carry_pending" not in s:
        try:
            conn.execute("ALTER TABLE member_settings ADD COLUMN carry_pending REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as e:  # noqa: PERF203
            # Deploy day restarts the API while a timer unit may be starting its
            # own job: both read the PRAGMA before either ALTERs, and the loser
            # would crash on boot. Only a column being added for the FIRST time
            # can race — every older column already exists in prod, so its
            # branch never runs.
            if "duplicate column" not in str(e).lower():
                raise
    # The CLUB SLICE's value, recorded alongside total account value from
    # 2026-08-24. The club's track record must measure the money the club
    # actually manages: total_value also carries personal positions, idle cash
    # and gifted shares, which inflated the record (services/clubnav).
    if "club_value" not in {r["name"] for r in conn.execute("PRAGMA table_info(equity_points)")}:
        conn.execute("ALTER TABLE equity_points ADD COLUMN club_value REAL")
    if "scored_at" not in cols("vote_sessions"):
        conn.execute("ALTER TABLE vote_sessions ADD COLUMN scored_at INTEGER")


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
