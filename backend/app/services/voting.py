"""Voting sessions + consensus.

Model: members "pick today's buys" from the 20-symbol universe. Each pick is one
row in `votes`. Consensus = the top-N most-picked symbols (ties broken by the
universe's sort_order, then alphabetically). Sessions move open → closed
(locked + picks chosen) → executed.
"""
from __future__ import annotations

import json
import time

from ..calendar_us import at_et, is_trading_day, next_trading_day, now_et, today_et
from ..config import settings
from ..db import audit, db


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
    a = active_session()
    if a and a["status"] == "open":
        return a
    td = today_et()
    if not is_trading_day(td) or now_et().timestamp() >= at_et(td, settings.vote_close_at).timestamp():
        td = next_trading_day(td)
    return open_session_for(td)


def get_session(session_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM vote_sessions WHERE id=?", (session_id,)).fetchone()
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
    clean: list[tuple[str, float]] = []
    for a in allocs:
        sym = str(a.get("symbol") or "").upper()
        try:
            w = float(a.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if sym in valid and sym not in seen and w > 0:
            seen.add(sym)
            clean.append((sym, max(0.0, min(100.0, w))))
    clean = clean[: settings.max_picks]
    total = sum(w for _, w in clean)
    if total > 100.0 and total > 0:                    # never let a member exceed 100%
        clean = [(s, w * 100.0 / total) for s, w in clean]
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM votes WHERE session_id=? AND member_id=?", (session_id, member_id))
        conn.executemany(
            "INSERT OR IGNORE INTO votes(session_id,member_id,symbol,weight,created_at) VALUES(?,?,?,?,?)",
            [(session_id, member_id, s, round(w, 4), now) for s, w in clean],
        )
    audit(f"member:{member_id}", "vote", sess["trade_date"],
          {"allocs": [{"symbol": s, "weight": round(w, 2)} for s, w in clean]})
    return [{"symbol": s, "weight": round(w, 4)} for s, w in clean]


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
    """This member's picks for a session as [{symbol, weight}] (weight = % of power)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, weight FROM votes WHERE session_id=? AND member_id=? ORDER BY weight DESC",
            (session_id, member_id)
        ).fetchall()
    return [{"symbol": r["symbol"], "weight": round(r["weight"], 2)} for r in rows]


def tally(session_id: int) -> list[dict]:
    """[{symbol, name, votes, weight}] for VOTED symbols, by voting-power weight.

    Each member's pick contributes their voting power (= account equity $), with
    a floor of 1 so members who haven't connected/snapshotted yet still count.
    The forming basket = the top-N by weight. (Universe is the whole S&P 500, so
    we only return symbols that actually got votes.)
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT v.symbol, COUNT(*) AS votes, "
            "       SUM(MAX(COALESCE(s.voting_power,0), 1.0) * COALESCE(v.weight,100)/100.0) AS weight, "
            "       u.name "
            "FROM votes v "
            "LEFT JOIN member_settings s ON s.member_id=v.member_id "
            "LEFT JOIN universe u ON u.symbol=v.symbol "
            "WHERE v.session_id=? GROUP BY v.symbol "
            "ORDER BY weight DESC, votes DESC, v.symbol",
            (session_id,),
        ).fetchall()
    return [{"symbol": r["symbol"], "name": r["name"] or r["symbol"],
             "votes": r["votes"], "weight": round(r["weight"] or 0, 2)} for r in rows]


def voter_count(session_id: int) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT member_id) AS n FROM votes WHERE session_id=?", (session_id,)
        ).fetchone()
    return row["n"] if row else 0


def close_and_select(session_id: int) -> dict:
    """Lock votes, choose the top-N consensus picks, persist them. Idempotent."""
    sess = get_session(session_id)
    if not sess:
        raise ValueError("no such session")
    if sess["status"] != "open":
        return sess  # already closed/executed
    ranked = tally(session_id)
    top_n = sess["top_n"]
    picks = [r for r in ranked if r["votes"] > 0][:top_n]
    picks_json = json.dumps(picks)
    with db() as conn:
        conn.execute(
            "UPDATE vote_sessions SET status='closed', closed_at=?, picks_json=? WHERE id=?",
            (int(time.time()), picks_json, session_id),
        )
        row = conn.execute("SELECT * FROM vote_sessions WHERE id=?", (session_id,)).fetchone()
    audit("system", "close_session", sess["trade_date"],
          {"picks": [p["symbol"] for p in picks], "voters": voter_count(session_id)})
    return dict(row)


def session_picks(session: dict) -> list[dict]:
    if session.get("picks_json"):
        return json.loads(session["picks_json"])
    return []


def session_public(session: dict, member_id: int | None) -> dict:
    """API view of a session, including the caller's own picks + the live tally."""
    return {
        "id": session["id"],
        "trade_date": session["trade_date"],
        "status": session["status"],
        "closes_at": session["closes_at"],
        "top_n": session["top_n"],
        "voters": voter_count(session["id"]),
        "tally": tally(session["id"]),
        "my_picks": member_picks(session["id"], member_id) if member_id else [],
        "picks": session_picks(session),
    }
