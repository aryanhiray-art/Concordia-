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
    return {u["symbol"] for u in universe()}


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


# ─── votes ───────────────────────────────────────────────────────────────────

def set_member_picks(session_id: int, member_id: int, symbols: list[str]) -> list[str]:
    """Replace a member's picks for a session. Raises if closed or symbol invalid."""
    sess = get_session(session_id)
    if not sess or sess["status"] != "open":
        raise ValueError("voting is closed for this session")
    valid = universe_symbols()
    picks = [s.upper() for s in symbols if s.upper() in valid]
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM votes WHERE session_id=? AND member_id=?", (session_id, member_id))
        conn.executemany(
            "INSERT OR IGNORE INTO votes(session_id,member_id,symbol,created_at) VALUES(?,?,?,?)",
            [(session_id, member_id, s, now) for s in picks],
        )
    audit(f"member:{member_id}", "vote", sess["trade_date"], {"picks": picks})
    return picks


def member_picks(session_id: int, member_id: int) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol FROM votes WHERE session_id=? AND member_id=?", (session_id, member_id)
        ).fetchall()
    return [r["symbol"] for r in rows]


def tally(session_id: int) -> list[dict]:
    """[{symbol, name, votes}] over the full universe, most-picked first."""
    order = {u["symbol"]: (u["sort_order"], u["name"]) for u in universe()}
    with db() as conn:
        rows = conn.execute(
            "SELECT symbol, COUNT(*) AS votes FROM votes WHERE session_id=? GROUP BY symbol",
            (session_id,),
        ).fetchall()
    counts = {r["symbol"]: r["votes"] for r in rows}
    out = []
    for sym, (so, name) in order.items():
        out.append({"symbol": sym, "name": name, "votes": counts.get(sym, 0)})
    out.sort(key=lambda x: (-x["votes"], order[x["symbol"]][0], x["symbol"]))
    return out


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
