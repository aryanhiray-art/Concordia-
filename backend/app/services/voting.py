"""Voting sessions + consensus.

Model: members "pick today's buys" from the 20-symbol universe. Each pick is one
row in `votes`. Consensus = the top-N most-picked symbols (ties broken by the
universe's sort_order, then alphabetically). Sessions move open → closed
(locked + picks chosen) → executed.
"""
from __future__ import annotations

import json
import sqlite3
import time

from ..calendar_us import at_et, is_sell_day, next_sell_day, now_et, today_et
from ..config import settings
from ..db import audit, db
from . import membership


# ─── universe ────────────────────────────────────────────────────────────────

def universe() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol,name,sort_order FROM universe WHERE active=1 ORDER BY sort_order,symbol"
        ).fetchall()
    return [dict(r) for r in rows]


def universe_symbols() -> set[str]:
    with db() as conn:
        rows = conn.execute("SELECT symbol FROM universe WHERE active=1").fetchall()
    return {r["symbol"] for r in rows}


def universe_search(q: str = "", limit: int = 40) -> list[dict]:
    """Search the S&P 500 universe by symbol or name. Exact symbol → prefix → substring."""
    q = (q or "").strip().upper()
    with db() as conn:
        if q:
            rows = conn.execute(
                "SELECT symbol,name FROM universe WHERE active=1 "
                "AND (symbol LIKE ? OR UPPER(name) LIKE ?) "
                "ORDER BY (symbol=?) DESC, (symbol LIKE ?) DESC, sort_order LIMIT ?",
                (f"%{q}%", f"%{q}%", q, f"{q}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbol,name FROM universe WHERE active=1 ORDER BY sort_order LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


# ─── session lifecycle ───────────────────────────────────────────────────────

def demo_roll_session() -> None:
    """DEMO_MODE only: keep an open ballot alive without any timers.

    The demo box runs none of the club's cron units, so its one seeded session
    expired and the Overview pill read "Cycle closes in 0:00" forever (owner
    report). On each ballot read: close an expired open session (normal tally,
    so it becomes real history) and open today's/tomorrow's with a future close.
    Idempotent and cheap; a no-op everywhere but demo_mode.
    """
    import datetime as _dt

    from ..calendar_us import ET
    from ..config import settings
    if not settings.demo_mode:
        return
    now = time.time()
    row = latest_session("open")
    if row and (row.get("closes_at") or 0) > now:
        return
    if row:
        try:
            close_and_select(row["id"])
        except Exception:  # noqa: BLE001
            pass
    today = _dt.datetime.now(ET).date()
    # Open the first upcoming date whose VOTE_CLOSE_AT is still in the FUTURE, so
    # the fresh demo ballot always has a live countdown. today@VOTE_CLOSE_AT is
    # already past whenever we roll after that time (e.g. any time after 08:30
    # ET) — opening for `today` then would create a ballot that reads 'expired'
    # the instant it exists, which is the bug this rolls past. Probe a few days
    # out so a holiday/weekend row already occupying tomorrow can't strand us.
    for probe in (today, today + _dt.timedelta(days=1), today + _dt.timedelta(days=2)):
        closes_at = int(at_et(probe, settings.vote_close_at).timestamp())
        if closes_at <= now:
            continue  # this date's close has already passed — never open expired
        with db() as conn:
            hit = conn.execute("SELECT status FROM vote_sessions WHERE trade_date=?",
                               (probe.isoformat(),)).fetchone()
        if hit is None:
            # open_session_for takes the DATE OBJECT — it isoformat()s
            # internally. Passing the string 500'd every /api/vote/today on the
            # demo (AttributeError) until a reviewer hot-patched the box; this
            # is the upstream commit of that fix.
            open_session_for(probe)
            return
        if hit["status"] == "open":
            return  # a live future ballot already exists for this date


def open_session_for(trade_date) -> dict:
    """Create (or return) an OPEN session for a trade date. Used by open_vote job."""
    td = trade_date.isoformat()
    closes_at = int(at_et(trade_date, settings.vote_close_at).timestamp())
    with db() as conn:
        existing = conn.execute("SELECT * FROM vote_sessions WHERE trade_date=?", (td,)).fetchone()
        if existing:
            return dict(existing)
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n) "
            "VALUES(?,?,?,?,?)",
            (td, "open", int(time.time()), closes_at, settings.top_n),
        )
        sid = cur.lastrowid
        row = conn.execute("SELECT * FROM vote_sessions WHERE id=?", (sid,)).fetchone()
    audit("system", "open_session", td, {"closes_at": closes_at, "top_n": settings.top_n})
    return dict(row)


def active_session() -> dict | None:
    """The session members are currently voting in (latest open), else latest."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM vote_sessions WHERE status='open' ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM vote_sessions ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def ensure_active_session() -> dict:
    """Make sure there is a votable session right now (used on-demand / by jobs).

    If today is a trading day and not yet past close, open today's session;
    otherwise open the next trading day's session.
    """
    demo_roll_session()   # no-op outside DEMO_MODE (no timers run there)
    a = active_session()
    if a and a["status"] == "open":
        return a
    td = today_et()
    # The votable session is the upcoming SELL/vote day's. Open today's only if
    # today is a sell day and we're before its close; else the next sell day's.
    if not is_sell_day(td) or now_et().timestamp() >= at_et(td, settings.vote_close_at).timestamp():
        td = next_sell_day(td)
    return open_session_for(td)


def get_session(session_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM vote_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def latest_session(status: str) -> dict | None:
    """Most recent session in a given status (e.g. 'closed' = sold, awaiting buy)."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM vote_sessions WHERE status=? ORDER BY trade_date DESC LIMIT 1", (status,)
        ).fetchone()
    return dict(row) if row else None


def session_for_trade_date(trade_date: str) -> dict | None:
    """The session for a specific trade date (YYYY-MM-DD, ET).

    Close/execute MUST target the day they run on — `active_session()` returns the
    latest session, which after a close is the *next* trading day's (just-opened)
    ballot. Executing that would trade an empty/future basket. Always resolve by
    today's date instead.
    """
    with db() as conn:
        row = conn.execute("SELECT * FROM vote_sessions WHERE trade_date=?", (trade_date,)).fetchone()
    return dict(row) if row else None


# ─── votes ───────────────────────────────────────────────────────────────────

def set_member_allocations(session_id: int, member_id: int, allocs: list[dict]) -> list[dict]:
    """Replace a member's picks + the % of their voting power on each (Concordia model).

    `allocs` = [{symbol, weight}] where weight is a 0–100 percentage. Total is
    capped at 100% (scaled down if a client over-allocates); a total under 100%
    just means the member abstains the remainder. Returns the stored allocations.
    """
    sess = get_session(session_id)
    if not sess or sess["status"] != "open":
        raise ValueError("voting is closed for this session")
    valid = universe_symbols()
    seen: set[str] = set()
    clean: list[tuple[str, float, str | None]] = []
    for a in allocs:
        sym = str(a.get("symbol") or "").upper()
        try:
            w = float(a.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if sym in valid and sym not in seen and w > 0:
            seen.add(sym)
            clean.append((sym, max(0.0, min(100.0, w)), _clean_thesis(a.get("thesis"))))
    clean = clean[: settings.max_picks]
    total = sum(w for _, w, _t in clean)
    if total > 100.0 and total > 0:                    # never let a member exceed 100%
        clean = [(s, w * 100.0 / total, t) for s, w, t in clean]
    now = int(time.time())
    # TOCTOU-safe (finding #16): take the write lock, re-check 'open' INSIDE the
    # transaction, and do the DELETE+INSERT atomically. A vote that read 'open'
    # microseconds before close_and_select commits can no longer land against a
    # just-closed session, and a concurrent tally reader never sees the member
    # with zero votes mid-rewrite.
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM vote_sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row["status"] != "open":
                raise ValueError("voting is closed for this session")
            conn.execute("DELETE FROM votes WHERE session_id=? AND member_id=?", (session_id, member_id))
            conn.executemany(
                "INSERT OR IGNORE INTO votes(session_id,member_id,symbol,weight,thesis,created_at) "
                "VALUES(?,?,?,?,?,?)",
                [(session_id, member_id, s, round(w, 4), t, now) for s, w, t in clean],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    audit(f"member:{member_id}", "vote", sess["trade_date"],
          {"allocs": [{"symbol": s, "weight": round(w, 2)} for s, w, _t in clean]})
    return [{"symbol": s, "weight": round(w, 4), "thesis": t} for s, w, t in clean]


def set_member_picks(session_id: int, member_id: int, symbols: list[str]) -> list[dict]:
    """Back-compat: equal-weight a bare list of symbols across the member's 100%."""
    syms: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        u = s.upper()
        if u not in seen:
            seen.add(u)
            syms.append(u)
    syms = syms[: settings.max_picks]
    if not syms:
        return set_member_allocations(session_id, member_id, [])
    w = 100.0 / len(syms)
    return set_member_allocations(session_id, member_id, [{"symbol": s, "weight": w} for s in syms])


def member_picks(session_id: int, member_id: int) -> list[dict]:
    """This member's picks for a session as [{symbol, weight, thesis}]."""
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, weight, thesis FROM votes WHERE session_id=? AND member_id=? ORDER BY weight DESC",
            (session_id, member_id)
        ).fetchall()
    return [{"symbol": r["symbol"], "weight": round(r["weight"], 2), "thesis": r["thesis"]}
            for r in rows]


_THESIS_MAX = 280


def _clean_thesis(raw) -> str | None:
    """Sanitize a member-typed rationale: strip control chars, collapse runs of
    whitespace, cap the length. Empty → None (stored as NULL, never '')."""
    if raw is None:
        return None
    txt = "".join(ch for ch in str(raw) if ch == " " or (ch.isprintable() and ch not in "\u2028\u2029"))
    txt = " ".join(txt.split()).strip()
    return txt[:_THESIS_MAX] or None


def symbol_theses(session_id: int, per_symbol: int = 3) -> dict[str, list[dict]]:
    """{symbol: [{name, text}]} — the latest few HUMAN rationales per pick.

    Bots never surface here (their generated theses stay admin-only), and a
    symbol with no rationale simply has no key — the payload stays lean."""
    with db() as conn:
        rows = conn.execute(
            "SELECT v.symbol, v.thesis, m.display_name AS name FROM votes v "
            "JOIN members m ON m.id = v.member_id "
            "WHERE v.session_id=? AND v.thesis IS NOT NULL AND COALESCE(m.is_bot,0)=0 "
            "ORDER BY v.created_at DESC", (session_id,)
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        bucket = out.setdefault(r["symbol"], [])
        if len(bucket) < per_symbol:
            bucket.append({"name": r["name"], "text": r["thesis"]})
    return out


def _tally_rows(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """Per-symbol vote rows with total weight + the bot slice of that weight.

    DEPARTED members are excluded by IDENTITY (membership.active_clause), not by
    deleting their ballots. A member who has left must not put a ticker into a
    basket that gets traded with other people's money — and because voting_power
    is floored at 1.0 below, a departed member with zero power still counted as a
    full vote. Excluding at tally time rather than deleting the row keeps the
    ballot auditable and means opting out never destroys a vote already cast.
    """
    return conn.execute(
        "SELECT v.symbol, COUNT(*) AS votes, "
        "       SUM(MAX(COALESCE(s.voting_power,0), 1.0) * COALESCE(v.weight,100)/100.0) AS weight, "
        "       SUM(CASE WHEN COALESCE(m.is_bot,0)=1 "
        "           THEN MAX(COALESCE(s.voting_power,0), 1.0) * COALESCE(v.weight,100)/100.0 "
        "           ELSE 0 END) AS bot_weight, "
        "       u.name "
        "FROM votes v "
        "LEFT JOIN member_settings s ON s.member_id=v.member_id "
        "JOIN members m ON m.id=v.member_id "
        "LEFT JOIN universe u ON u.symbol=v.symbol "
        f"WHERE v.session_id=? AND {membership.active_clause('m')} GROUP BY v.symbol "
        "ORDER BY weight DESC, votes DESC, v.symbol",
        (session_id,),
    ).fetchall()


def tally(session_id: int, conn: sqlite3.Connection | None = None) -> list[dict]:
    """[{symbol, name, votes, weight}] for VOTED symbols, by voting-power weight.

    Each member's pick contributes their voting power (= account equity $), with
    a floor of 1 so members who haven't connected/snapshotted yet still count.
    The forming basket = the top-N by weight. (Universe is the whole S&P 500, so
    we only return symbols that actually got votes.)

    Bot containment: if the bots' AGGREGATE weight exceeds
    settings.bot_tally_share_max of the total, every bot contribution is scaled
    by the single factor that brings the bots exactly to that share. Human
    weights are never touched. `conn` lets a caller (close_and_select) tally
    inside its own transaction; default None opens a fresh connection.
    """
    if conn is None:
        with db() as own:
            rows = _tally_rows(own, session_id)
    else:
        rows = _tally_rows(conn, session_id)
    entries = [{"symbol": r["symbol"], "name": r["name"] or r["symbol"],
                "votes": r["votes"], "weight": float(r["weight"] or 0),
                "_bot": float(r["bot_weight"] or 0)} for r in rows]
    share_max = settings.bot_tally_share_max
    bot_total = sum(e["_bot"] for e in entries)
    human_total = sum(e["weight"] - e["_bot"] for e in entries)
    total = bot_total + human_total
    if 0 < share_max < 1 and total > 0 and human_total > 0 and bot_total / total > share_max:
        # factor f: f·B / (f·B + H) == share_max  →  f = share·H / ((1−share)·B)
        f = share_max * human_total / ((1.0 - share_max) * bot_total)
        for e in entries:
            e["weight"] = (e["weight"] - e["_bot"]) + e["_bot"] * f
        entries.sort(key=lambda e: (-e["weight"], -e["votes"], e["symbol"]))
    return [{"symbol": e["symbol"], "name": e["name"],
             "votes": e["votes"], "weight": round(e["weight"], 2)} for e in entries]


def voter_count(session_id: int) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT member_id) AS n FROM votes WHERE session_id=?", (session_id,)
        ).fetchone()
    return row["n"] if row else 0


def human_voter_count(session_id: int) -> int:
    """Distinct HUMAN (non-bot) members who voted in this session.

    The execution human-quorum floor (finding #10) uses this: a basket chosen on
    a zero-human-turnout day is bot-only and must never be bought with real money.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT v.member_id) AS n FROM votes v "
            "JOIN members m ON m.id=v.member_id "
            # The tally already drops departed ballots; the quorum gate must
            # agree, or a basket with no live human behind it still executes.
            f"WHERE v.session_id=? AND COALESCE(m.is_bot,0)=0 AND {membership.active_clause('m')}",
            (session_id,),
        ).fetchone()
    return row["n"] if row else 0


def member_has_live_ballot(member_id: int) -> bool:
    """Has this member voted in a session that has not executed yet?

    Broader than member_in_closed_unexecuted_session, which only covers the
    CLOSED-awaiting-execution window. Participation must be frozen for the whole
    life of a ballot the member has voted in: while a session is still OPEN their
    pick already carries full weight in the tally (voting._tally_rows keys off
    voting_power, not opted_in), but executor._opted_members funds only
    opted_in=1 members. Opting out mid-ballot therefore let a member choose the
    basket and pay for none of it — the cost landing on whoever stayed in.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM votes v JOIN vote_sessions s ON s.id = v.session_id "
            "WHERE v.member_id=? AND s.status != 'executed' LIMIT 1",
            (member_id,)).fetchone()
    return row is not None


def member_in_closed_unexecuted_session(member_id: int) -> bool:
    """True if this member voted in a session that is CLOSED but not yet EXECUTED.

    During that window the member's vote weight is already frozen into the locked
    basket but the money hasn't been deployed — so their allocation must not be
    editable, else they could inflate power at close and withdraw stake before
    execute (free-rider, finding #9).
    """
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM vote_sessions vs JOIN votes v ON v.session_id=vs.id "
            "WHERE vs.status='closed' AND v.member_id=? LIMIT 1",
            (member_id,),
        ).fetchone()
    return row is not None


def close_and_select(session_id: int) -> dict:
    """Lock votes, choose the top-N consensus picks, persist them. Idempotent.

    Concurrency-safe: the close can race (close timer + admin button + execute
    job). One connection takes the write lock up front (BEGIN IMMEDIATE),
    re-reads the status inside the transaction, and the UPDATE is guarded with
    `AND status='open'` — if another closer already won, this call returns that
    closer's row unchanged and writes nothing.
    """
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            sess = conn.execute(
                "SELECT * FROM vote_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not sess:
                raise ValueError("no such session")
            if sess["status"] != "open":
                conn.execute("COMMIT")
                return dict(sess)  # already closed/executed
            picks = [r for r in tally(session_id, conn=conn) if r["votes"] > 0][: sess["top_n"]]
            cur = conn.execute(
                "UPDATE vote_sessions SET status='closed', closed_at=?, picks_json=? "
                "WHERE id=? AND status='open'",
                (int(time.time()), json.dumps(picks), session_id),
            )
            we_closed = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM vote_sessions WHERE id=?", (session_id,)
            ).fetchone()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    if not we_closed:
        return dict(row)  # another closer won the race — return their result
    audit("system", "close_session", row["trade_date"],
          {"picks": [p["symbol"] for p in picks], "voters": voter_count(session_id)})
    # Capture entry prices for accuracy scoring a cycle later. Runs regardless of
    # POWER_MODEL so the price history is already there whenever blend is switched
    # on. Best-effort — never let a quote hiccup fail the close.
    try:
        from . import reputation
        reputation.capture_cycle_prices(session_id)
    except Exception:  # noqa: BLE001
        pass
    return dict(row)


def session_picks(session: dict) -> list[dict]:
    if session.get("picks_json"):
        return json.loads(session["picks_json"])
    return []


# An 'open' row whose close time is more than a day past was abandoned
# mid-cycle — prod carries one from 2026-06-22. It is cruft, not a live ballot.
STALE_OPEN_AFTER_SECS = 86400


def is_abandoned_open(session) -> bool:
    """True for a session still marked 'open' whose closes_at is over a day past.

    The single source of the staleness rule: adminview (which ballot's quorum
    counts) and the history views (which ballot may present as open) must agree,
    or a dead 2026-06-22 row masquerades as today's cycle in one of them.
    Accepts any row-like (dict or sqlite3.Row).
    """
    try:
        status, closes_at = session["status"], session["closes_at"]
    except (KeyError, IndexError, TypeError):
        return False
    return status == "open" and bool(closes_at) and closes_at < time.time() - STALE_OPEN_AFTER_SECS


def _latest_decided_session_id() -> int | None:
    """The one decided session whose cycle can still be in flight.

    The executor's buy leg only ever processes the LATEST decided cycle, so an
    OLDER row stuck at status='closed' (its buy day never completed) can never
    run — its cycle is over. Used for DISPLAY derivation only; vote_sessions
    itself is trading data and is never mutated from here.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM vote_sessions WHERE status IN ('closed','executed') "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return row["id"] if row else None


def _status_display(session, latest_decided_id: int | None) -> str:
    """Presentation status: 'expired' for an abandoned open ballot, 'executed'
    for a STALE closed session (see _latest_decided_session_id), else the raw
    status unchanged. Derivation only — vote_sessions is never mutated."""
    if is_abandoned_open(session):
        return "expired"
    status = session["status"]
    if status == "closed" and session["id"] != latest_decided_id:
        return "executed"
    return status


def club_history(limit: int = 30) -> list[dict]:
    """The club's shared record: DECIDED sessions (closed/executed), newest first.

    The fund's history is not unique per person — every member sees the same
    record, so nothing here may identify a member (no emails, names, ids, or
    per-member execution rows). It is also OPERATIONS-FREE by contract: no
    voter counts, no human-vs-bot split, no per-status order counts — a member
    sees the clean club record (date · status · basket) and nothing about how
    the sausage was made. The operational per-cycle view lives at
    GET /api/admin/cycles (admin-only). Capped at 60 sessions.
    """
    limit = max(1, min(int(limit), 60))
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM vote_sessions WHERE status IN ('closed','executed') "
            "ORDER BY trade_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    latest_decided = _latest_decided_session_id()
    return [{
        "id": s["id"],
        "trade_date": s["trade_date"],
        "status": s["status"],
        "status_display": _status_display(s, latest_decided),
        "basket": [p["symbol"] for p in session_picks(dict(s))],
    } for s in rows]


def member_vote_history(member_id: int, limit: int = 30) -> list[dict]:
    """The CALLER's ballots, newest first — only sessions this member voted in.

    A member's votes are theirs alone, so an OPEN session's ballot is included
    for its owner — but with basket/hits empty until the ballot closes. Pre-close
    the only vote anyone may see is their own (same rule as session_public's
    my_picks vs the aggregate tally), and the decided basket doesn't exist yet.
    Capped at 60 sessions.
    """
    limit = max(1, min(int(limit), 60))
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM vote_sessions WHERE id IN "
            "(SELECT session_id FROM votes WHERE member_id=?) "
            "ORDER BY trade_date DESC LIMIT ?",
            (member_id, limit),
        ).fetchall()
    latest_decided = _latest_decided_session_id()
    out: list[dict] = []
    for s in rows:
        mine = member_picks(s["id"], member_id)
        # Belt-and-braces: gate on status, not just picks_json presence — an
        # open session must never show a basket, whatever the row contains.
        decided = s["status"] in ("closed", "executed")
        basket = [p["symbol"] for p in session_picks(dict(s))] if decided else []
        chosen = set(basket)
        out.append({
            "id": s["id"],
            "trade_date": s["trade_date"],
            "status": s["status"],
            "status_display": _status_display(s, latest_decided),
            "my_allocations": mine,
            "basket": basket,
            "hits": [a["symbol"] for a in mine if a["symbol"] in chosen],
        })
    return out


def _merge_tallies(base: list[dict], extra: list[dict]) -> list[dict]:
    """Sum two aggregate tallies by symbol (votes + weight), sorted like tally().
    base=[] -> extra unchanged, so every non-demo path is byte-identical."""
    if not base:
        return extra
    by: dict[str, dict] = {}
    for row in list(base) + list(extra):
        sym = row.get("symbol")
        if not sym:
            continue
        hit = by.setdefault(sym, {"symbol": sym, "name": row.get("name") or sym,
                                  "votes": 0, "weight": 0.0})
        hit["votes"] += int(row.get("votes") or 0)
        hit["weight"] = round(hit["weight"] + float(row.get("weight") or 0.0), 2)
    return sorted(by.values(), key=lambda r: (-r["weight"], -r["votes"], r["symbol"]))


def _attach_theses(rows: list[dict], session_id: int) -> list[dict]:
    """Fold member rationales onto tally rows (post-merge: mirror rows carry
    none, so attachment must happen after the demo overlay)."""
    try:
        theses = symbol_theses(session_id)
    except Exception:  # noqa: BLE001 — rationale display never breaks the ballot
        return rows
    if not theses:
        return rows
    for r in rows:
        t = theses.get(r.get("symbol"))
        if t:
            r["theses"] = t
    return rows


def session_public(session: dict, member_id: int | None) -> dict:
    """API view of a session, including the caller's own picks + the live tally."""
    # DEMO ONLY: overlay the LIVE club's forming tally so a fresh demo ballot
    # never reads "0 voters" (the real decisions come from the live club; the
    # demo's local votes are merged on top so a demo voter still sees their
    # own mark land). No-op outside demo_mode or when no mirror is present.
    live_tally: list[dict] = []
    live_voters = 0
    try:
        from ..config import settings as _settings
        if _settings.demo_mode:
            from . import clubmirror as _cm
            t = _cm.today()
            if t:
                live_tally = list(t.get("tally") or [])
                live_voters = int(t.get("voters") or 0)
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": session["id"],
        "trade_date": session["trade_date"],
        "status": session["status"],
        "closes_at": session["closes_at"],
        "top_n": session["top_n"],
        # HUMAN voters only: the raw count includes bot members (test
        # scaffolding), and a bot-inflated number on every member's Vote page
        # was an operational tell. Total-with-bots stays admin-only
        # (adminview cycle_view / GET /api/admin/cycles).
        "voters": human_voter_count(session["id"]) + live_voters,
        "tally": _attach_theses(_merge_tallies(live_tally, tally(session["id"])), session["id"]),
        "my_picks": member_picks(session["id"], member_id) if member_id else [],
        "picks": session_picks(session),
    }
