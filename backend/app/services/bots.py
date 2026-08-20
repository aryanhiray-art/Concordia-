"""AI voting bots: seed, daily voting, and weekly accuracy re-weighting.

Bots are `members` (is_bot=1) with no Robinhood account, so the executor never
trades for them — they only vote, and their votes are weighted by voting power
exactly like a human's. Each starts at $100 of power. Once a week their power is
re-snapshotted the SAME way members' power is (a row in voting_power_history with
a week-over-week delta) — except a bot's new power compounds by how its picks
actually performed: new_power = old_power x (1 + realized_return). Accurate bots
gain influence; wrong bots lose it.

Containment: a bot's power is clamped to [floor, settings.bot_power_cap] so an
accurate streak can't compound a bot into dominating the tally; the tally also
caps the bots' AGGREGATE share (settings.bot_tally_share_max) in voting.tally.
"""
from __future__ import annotations

import datetime as _dt
import time

from ..bots import ollama
from ..bots.strategies import STRATEGIES, bot_email
from ..config import settings
from ..db import audit, db
from . import market, power, voting

_BY_SLUG = {s["slug"]: s for s in STRATEGIES}
_POWER_FLOOR = 10.0   # a bot never drops below this, so it keeps a small voice


# ─── seed ────────────────────────────────────────────────────────────────────

def seed_bots() -> int:
    """Create any missing bots (idempotent). Returns how many were created."""
    now = int(time.time())
    week = power.week_start_et()
    created = 0
    with db() as conn:
        for s in STRATEGIES:
            email = bot_email(s["slug"])
            row = conn.execute("SELECT id FROM members WHERE email=?", (email,)).fetchone()
            if row:
                # keep an existing bot's model/strategy in sync with the code
                conn.execute(
                    "UPDATE members SET bot_model=?, bot_temperature=?, bot_strategy=? WHERE id=?",
                    (s["model"], s["temperature"], s["style"], row["id"]),
                )
                continue
            cur = conn.execute(
                "INSERT INTO members(email,display_name,role,is_bot,bot_strategy,bot_slug,"
                "bot_model,bot_temperature,created_at) VALUES(?,?,?,1,?,?,?,?,?)",
                (email, s["name"], "bot", s["style"], s["slug"], s["model"], s["temperature"], now),
            )
            mid = cur.lastrowid
            conn.execute(
                "INSERT INTO member_settings(member_id,opted_in,buying_power_pct,daily_cap_usd,"
                "max_orders,voting_power,alloc_type,alloc_value,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (mid, 1, 0, 0, 0, settings.bot_start_power, "dollar", 0, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO voting_power_history(member_id,week_start,power,equity,"
                "prev_power,delta,created_at) VALUES(?,?,?,?,?,?,?)",
                (mid, week, settings.bot_start_power, None, None, None, now),
            )
            created += 1
    if created:
        audit("system", "seed_bots", None, {"created": created})
    return created


def bot_members() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.display_name, m.bot_slug, m.bot_strategy, m.bot_model, "
            "m.bot_temperature, s.voting_power "
            "FROM members m JOIN member_settings s ON s.member_id=m.id "
            "WHERE m.is_bot=1 ORDER BY s.voting_power DESC, m.display_name"
        ).fetchall()
    return [dict(r) for r in rows]


def bots_view() -> list[dict]:
    """Per-bot console rows: power + accuracy + what each bot last voted for.

    DB-only (the LLM probe is the route's job — a network call has no place in a
    view builder other panels may reuse). last_vote_at comes from `votes` (the
    canonical ballot record); last_picks from the bot's newest bot_picks session,
    conviction-ordered — session ids are AUTOINCREMENT, so MAX(session_id) is the
    most recent session the bot actually voted in.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.display_name, s.voting_power, s.accuracy, "
            "(SELECT MAX(v.created_at) FROM votes v WHERE v.member_id=m.id) AS last_vote_at "
            "FROM members m LEFT JOIN member_settings s ON s.member_id=m.id "
            "WHERE COALESCE(m.is_bot,0)=1 ORDER BY s.voting_power DESC, m.display_name"
        ).fetchall()
        picks = conn.execute(
            "SELECT bp.member_id, bp.symbol FROM bot_picks bp "
            "WHERE bp.session_id=(SELECT MAX(b2.session_id) FROM bot_picks b2 "
            "                     WHERE b2.member_id=bp.member_id) "
            "ORDER BY bp.member_id, bp.weight_pct DESC, bp.symbol"
        ).fetchall()
    by_member: dict[int, list[str]] = {}
    for p in picks:
        by_member.setdefault(p["member_id"], []).append(p["symbol"])
    return [{
        "id": r["id"],
        "display_name": r["display_name"],
        "voting_power": float(r["voting_power"] or 0),
        "accuracy": float(r["accuracy"] or 0),
        "last_vote_at": r["last_vote_at"],
        "last_picks": by_member.get(r["id"], []),
    } for r in rows]


# ─── daily voting ────────────────────────────────────────────────────────────

def _universe_with_prices() -> tuple[list[str], dict[str, float]]:
    uni = voting.universe()[: settings.bots_universe_size]
    tickers = [u["symbol"] for u in uni]
    prices = market.quotes(tickers)
    return tickers, prices


def cast_votes(session: dict) -> dict:
    """Every bot decides + votes in the (open) session. Records picks for scoring."""
    tickers, prices = _universe_with_prices()
    now = int(time.time())
    sid = session["id"]
    voted, failed, abstained = 0, 0, 0
    details: list[dict] = []
    for b in bot_members():
        strat = _BY_SLUG.get(b["bot_slug"])
        if not strat:
            continue
        try:
            decision = ollama.decide(strat, tickers, prices)
        except Exception as e:  # noqa: BLE001 — a flaky model just abstains today
            failed += 1
            details.append({"bot": b["display_name"], "status": "error", "err": str(e)[:140]})
            continue
        picks = decision["picks"]
        if not picks:
            abstained += 1
            details.append({"bot": b["display_name"], "status": "abstained"})
            continue
        symbols = [p["ticker"] for p in picks]
        try:
            # bots allocate their voting power by conviction (their own pct weights)
            voting.set_member_allocations(sid, b["id"],
                                          [{"symbol": p["ticker"], "weight": p["pct"]} for p in picks])
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append({"bot": b["display_name"], "status": "vote_failed", "err": str(e)[:140]})
            continue
        with db() as conn:
            conn.execute("DELETE FROM bot_picks WHERE session_id=? AND member_id=?", (sid, b["id"]))
            conn.executemany(
                "INSERT OR IGNORE INTO bot_picks(session_id,member_id,symbol,weight_pct,"
                "entry_price,thesis,created_at) VALUES(?,?,?,?,?,?,?)",
                [(sid, b["id"], p["ticker"], p["pct"], prices.get(p["ticker"]),
                  decision["thesis"], now) for p in picks],
            )
        voted += 1
        details.append({"bot": b["display_name"], "status": "voted",
                        "picks": symbols, "thesis": decision["thesis"]})
    audit("system", "bot_votes", session["trade_date"],
          {"voted": voted, "failed": failed, "abstained": abstained})
    return {"trade_date": session["trade_date"], "voted": voted, "failed": failed,
            "abstained": abstained, "details": details}


# ─── weekly accuracy → voting power ──────────────────────────────────────────

def _bot_week_return(member_id: int, since_week: str, until_week: str,
                     prices: dict[str, float]) -> tuple[float | None, int]:
    """pct-weighted return of a bot's picks (entry price → now) over [since,until)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT bp.symbol, bp.weight_pct, bp.entry_price FROM bot_picks bp "
            "JOIN vote_sessions vs ON vs.id=bp.session_id "
            "WHERE bp.member_id=? AND vs.trade_date>=? AND vs.trade_date<?",
            (member_id, since_week, until_week),
        ).fetchall()
    num = den = 0.0
    n = 0
    for r in rows:
        entry = r["entry_price"]
        cur = prices.get(r["symbol"])
        w = r["weight_pct"] or 0.0
        if not entry or not cur or w <= 0:
            continue
        num += w * (cur / entry - 1.0)
        den += w
        n += 1
    if den <= 0:
        return None, 0
    return num / den, n


def score_and_reweight(week: str | None = None) -> dict:
    """Weekly job: re-snapshot every bot's voting power from last week's accuracy.

    `week` = the week being *entered* (Sunday). We score the picks made during the
    prior week and compound each bot's power by its realized return, recording a
    voting_power_history row + delta — the same shape as members' weekly snapshot.
    """
    if settings.power_model == "blend":
        # In blend mode the unified reputation model (reputation.snapshot_power_all)
        # scores AND weights bots on the same 50/50 formula as humans, so the legacy
        # multiplicative bot reweight is a no-op — avoid double-writing power.
        return {"week": power.week_start_et(), "scored_from": None, "bots": [],
                "note": "POWER_MODEL=blend — bots weighted by the unified reputation model"}
    this_week = week or power.week_start_et()
    prev_week = power.week_start_et(_dt.date.fromisoformat(this_week) - _dt.timedelta(days=1))
    prices = market.quotes([u["symbol"] for u in voting.universe()[: settings.bots_universe_size]])
    now = int(time.time())
    out: list[dict] = []
    for b in bot_members():
        mid = b["id"]
        # Baseline = the snapshot at the START of the scored week, NOT the mutable
        # current voting_power — so re-running this job is idempotent (no double
        # compounding). Falls back to the start power before the first snapshot.
        prev = power._prev_power(mid, this_week)
        old = float(prev) if prev is not None else float(settings.bot_start_power)
        ret, n = _bot_week_return(mid, prev_week, this_week, prices)
        if ret is None:
            # no scorable picks → power unchanged (but never above the cap)
            new = min(settings.bot_power_cap, old)
        else:
            new = min(settings.bot_power_cap,
                      max(_POWER_FLOOR, round(old * (1.0 + ret), 2)))
        delta = round(new - old, 2)
        with db() as conn:
            conn.execute(
                "INSERT INTO voting_power_history(member_id,week_start,power,equity,prev_power,"
                "delta,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(member_id,week_start) DO UPDATE "
                "SET power=excluded.power, equity=excluded.equity, prev_power=excluded.prev_power, "
                "delta=excluded.delta",
                (mid, this_week, new, (round(ret * 100, 2) if ret is not None else None),
                 old, delta, now),
            )
            conn.execute("UPDATE member_settings SET voting_power=?, updated_at=? WHERE member_id=?",
                         (new, now, mid))
        out.append({"bot": b["display_name"], "return_pct": (round(ret * 100, 2) if ret is not None else None),
                    "scored_picks": n, "old_power": round(old, 2), "new_power": new, "delta": delta})
    audit("system", "bot_reweight", this_week, {"bots": len(out)})
    return {"week": this_week, "scored_from": prev_week, "bots": out}
