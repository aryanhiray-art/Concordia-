"""Reputation & blended voting power — the ETHGlobal Concordia 50/50 formula.

    VP(i) = CAPITAL · CapitalShare(i) + ACCURACY · [ AccuracyShare(i) · confidence(i) ]
      CapitalShare(i)  = capital(i) / Σ capital            # committed stake $ (bots have none → 0)
      AccuracyShare(i) = max(acc(i),0) / Σ max(acc(j),0)   # negatives floored to 0
      confidence(i)    = min(cycles(i) / CONFIDENCE_CYCLES, 1)
      acc(i)           = EWMA(α) of the vote-weighted excess return vs the benchmark

Money AND merit, 50/50 by default. Accuracy is scored one cycle after each vote
from prices captured at close (paper basis — you're credited for what you BACKED,
even picks that lost the vote). The snapshot is peer-relative then rescaled to the
$ capital pool, so with no accuracy history yet the blend reduces to pure stake
(cold-start == legacy) and accuracy phases in over CONFIDENCE_CYCLES.

Enabled by POWER_MODEL=blend; dormant (prices still captured) otherwise. See the
hackathon repo cx18121/concordia — DESIGN.md §3, Governance.sol, scoring.ts.
"""
from __future__ import annotations

import time

from ..config import settings
from ..db import audit, db
from . import alloc, market
from . import power as power_svc


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── price capture (at session close) ─────────────────────────────────────────

def capture_cycle_prices(session_id: int) -> dict:
    """Snapshot the price of every VOTED symbol + the benchmark at close — the
    'entry' a cycle's picks are scored against. Runs regardless of POWER_MODEL so
    the history is already there whenever blend is switched on. Best-effort."""
    with db() as conn:
        syms = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM votes WHERE session_id=?", (session_id,)).fetchall()]
    want = sorted(set(s.upper() for s in syms) | {settings.benchmark_symbol})
    try:
        px = market.quotes(want)
    except Exception:  # noqa: BLE001
        px = {}
    now = int(time.time())
    n = 0
    with db() as conn:
        for s in want:
            p = px.get(s.upper())
            if p and p > 0:
                conn.execute(
                    "INSERT INTO cycle_prices(session_id,symbol,price,captured_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(session_id,symbol) DO UPDATE SET price=excluded.price, captured_at=excluded.captured_at",
                    (session_id, s.upper(), p, now))
                n += 1
    return {"session_id": session_id, "captured": n, "wanted": len(want)}


def _prices_for(session_id: int) -> dict[str, float]:
    with db() as conn:
        return {r["symbol"]: r["price"] for r in conn.execute(
            "SELECT symbol, price FROM cycle_prices WHERE session_id=?", (session_id,)).fetchall()}


# ── accuracy scoring (a cycle after the vote) ────────────────────────────────

def _alpha() -> float:
    return settings.ewma_alpha_bps / 1e4


def score_cycle(scored_session_id: int, exit_prices: dict[str, float]) -> dict:
    """EWMA-update every voter's accuracy from session `scored`'s vote-weighted
    excess return vs the benchmark. entry = that session's captured prices,
    exit = `exit_prices`. Idempotency (scored_at) is the caller's responsibility."""
    entry = _prices_for(scored_session_id)
    bench = settings.benchmark_symbol
    be, bx = entry.get(bench), exit_prices.get(bench)
    if not be or not bx:
        # No benchmark price on one side → can't compute excess-vs-benchmark. SKIP the
        # whole cycle (caller must NOT set scored_at) so it retries with real data,
        # rather than silently scoring RAW return (sp_ret=0) and sealing it.
        return {"scored_session": scored_session_id, "scored": False,
                "reason": "missing_benchmark_price", "members": []}
    sp_ret = bx / be - 1.0
    with db() as conn:
        rows = conn.execute(
            "SELECT member_id, symbol, weight FROM votes WHERE session_id=?", (scored_session_id,)).fetchall()
    by_member: dict[int, list[tuple[str, float]]] = {}
    for r in rows:
        by_member.setdefault(r["member_id"], []).append((r["symbol"].upper(), r["weight"] or 0.0))
    a = _alpha()
    scored = []
    for mid, picks in by_member.items():
        this = 0.0
        used = 0
        for sym, w in picks:
            e, x = entry.get(sym), exit_prices.get(sym)
            if not e or not x or w <= 0:
                continue
            this += (w / 100.0) * ((x / e - 1.0) - sp_ret)   # vote-weighted excess vs benchmark
            used += 1
        if used == 0:
            # Not a single pick had both an entry AND exit price → this is a NON-
            # measurement, not "the member scored 0". Do NOT decay their accuracy or
            # ramp their confidence on it.
            scored.append({"member_id": mid, "picks_scored": 0, "skipped": "no_measurable_picks"})
            continue
        with db() as conn:
            row = conn.execute("SELECT accuracy FROM member_settings WHERE member_id=?", (mid,)).fetchone()
            old = float(row["accuracy"]) if row and row["accuracy"] is not None else 0.0
            new = a * this + (1 - a) * old
            conn.execute(
                "UPDATE member_settings SET accuracy=?, cycles_participated=cycles_participated+1, updated_at=? "
                "WHERE member_id=?", (new, int(time.time()), mid))
        scored.append({"member_id": mid, "cycle_excess_pct": round(this * 100, 3),
                       "accuracy": round(new, 5), "picks_scored": used})
    return {"scored_session": scored_session_id, "scored": True,
            "benchmark_return_pct": round(sp_ret * 100, 3), "members": scored}


def score_matured_cycles() -> list[dict]:
    """Score every unscored session that has entry prices AND a later session with
    prices to serve as the exit (a clean one-cycle window). Idempotent via scored_at."""
    with db() as conn:
        sessions = [dict(r) for r in conn.execute(
            "SELECT id, trade_date FROM vote_sessions WHERE scored_at IS NULL "
            "AND id IN (SELECT DISTINCT session_id FROM cycle_prices) ORDER BY trade_date").fetchall()]
    out = []
    for s in sessions:
        with db() as conn:
            nxt = conn.execute(
                "SELECT id FROM vote_sessions WHERE trade_date > ? "
                "AND id IN (SELECT DISTINCT session_id FROM cycle_prices) ORDER BY trade_date LIMIT 1",
                (s["trade_date"],)).fetchone()
        if not nxt:
            continue   # not matured yet — no exit prices captured for a later cycle
        res = score_cycle(s["id"], _prices_for(nxt["id"]))
        if not res.get("scored"):
            continue   # e.g. missing benchmark price — leave unscored so it retries
        with db() as conn:
            # Claim atomically (guard scored_at IS NULL) so a concurrent run / retry
            # can't double-apply the non-idempotent EWMA to the same cycle.
            conn.execute("UPDATE vote_sessions SET scored_at=? WHERE id=? AND scored_at IS NULL",
                         (int(time.time()), s["id"]))
        out.append(res)
    if out:
        audit("system", "accuracy_scored", None, {"cycles": len(out)})
    return out


# ── blended power snapshot ───────────────────────────────────────────────────

def _member_capital(m: dict) -> float:
    """Committed stake ($) — the CapitalShare numerator. Bots hold no real money;
    while they're test stand-ins for humans they carry a FAKE stake (bot_stake_usd)
    so they vote as funded members. Set BOT_STAKE_USD=0 to make bots pure-accuracy."""
    if m["is_bot"]:
        return float(settings.bot_stake_usd)
    bal = alloc.member_balance(m["member_id"])
    if not bal:
        return 0.0
    return alloc.rebalance_budget(m["alloc_type"], m["alloc_value"],
                                  bal.get("total_value"), bal.get("buying_power")) or 0.0


def _member_equity(m: dict) -> float:
    """Real account value ($) for the history/graph row (bots = their fake stake)."""
    if m["is_bot"]:
        return float(settings.bot_stake_usd)
    bal = alloc.member_balance(m["member_id"])
    return ((bal.get("total_value") if bal else None) or 0.0)


def _member_snapshot_inputs(m: dict) -> tuple[float, float, bool]:
    """(capital, equity, ok) from ONE balance fetch. ok=False means a CONNECTED
    member's live balance read FAILED (transient Robinhood error) — the caller must
    SKIP the write and carry the member's prior power, never persist a 0 from a
    failed read (that would collapse a funded member's vote for a whole week)."""
    if m["is_bot"]:
        fs = float(settings.bot_stake_usd)
        return fs, fs, True
    if (m.get("rh_status") or "") != "connected":
        return 0.0, 0.0, True   # legitimately uncommitted — no connected account, genuine 0
    bal = alloc.member_balance(m["member_id"])
    if not bal:
        return 0.0, 0.0, False  # connected but the read FAILED — do NOT zero their power
    cap = alloc.rebalance_budget(m["alloc_type"], m["alloc_value"],
                                 bal.get("total_value"), bal.get("buying_power")) or 0.0
    return cap, (bal.get("total_value") or 0.0), True


def snapshot_power_all() -> list[dict]:
    """Blended VP for every member. Peer-relative shares, then rescaled to the $
    capital pool so numbers stay dollar-readable and cold-start (no accuracy) == the
    member's raw stake. Writes voting_power + a weekly history row (legacy shape)."""
    with db() as conn:
        members = [dict(r) for r in conn.execute(
            "SELECT m.id AS member_id, COALESCE(m.is_bot,0) AS is_bot, s.alloc_type, s.alloc_value, "
            "       COALESCE(s.accuracy,0) AS accuracy, COALESCE(s.cycles_participated,0) AS cycles_participated, "
            "       a.status AS rh_status "
            "FROM members m JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id").fetchall()]
    inputs = {m["member_id"]: _member_snapshot_inputs(m) for m in members}
    # FAIL-SAFE: drop members whose live balance read failed — carry their prior power.
    active = [m for m in members if inputs[m["member_id"]][2]]
    skipped = [m["member_id"] for m in members if not inputs[m["member_id"]][2]]
    caps = {m["member_id"]: max(0.0, inputs[m["member_id"]][0]) for m in active}
    eqs = {m["member_id"]: inputs[m["member_id"]][1] for m in active}   # real account value for history/graph
    total_cap = sum(caps.values())
    total_pos_acc = sum(max(0.0, float(m["accuracy"])) for m in active)
    cap_w = settings.capital_bps / 1e4
    acc_w = settings.accuracy_bps / 1e4
    conf_n = max(1, settings.confidence_cycles)

    raw: dict[int, float] = {}
    for m in active:
        mid = m["member_id"]
        cap_share = (caps[mid] / total_cap) if total_cap > 0 else 0.0
        acc_share = (max(0.0, float(m["accuracy"])) / total_pos_acc) if total_pos_acc > 0 else 0.0
        conf = min(float(m["cycles_participated"]) / conf_n, 1.0)
        raw[mid] = cap_w * cap_share + acc_w * acc_share * conf
    total_raw = sum(raw.values())

    week = power_svc.week_start_et()
    now = int(time.time())
    out = []
    for m in active:
        mid = m["member_id"]
        # rescale to the $ pool: cold-start (all accuracy 0) → power == raw stake
        vp = round((total_cap * raw[mid] / total_raw) if total_raw > 0 else caps[mid], 2)
        prev = power_svc._prev_power(mid, week)
        delta = (vp - prev) if prev is not None else None
        with db() as conn:
            conn.execute(
                "INSERT INTO voting_power_history(member_id,week_start,power,equity,prev_power,delta,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(member_id,week_start) DO UPDATE SET "
                "power=excluded.power, equity=excluded.equity, prev_power=excluded.prev_power, delta=excluded.delta",
                (mid, week, vp, round(eqs[mid], 2), prev, delta, now))
            conn.execute("UPDATE member_settings SET voting_power=?, updated_at=? WHERE member_id=?",
                         (vp, now, mid))
        out.append({"member_id": mid, "is_bot": bool(m["is_bot"]), "capital": round(caps[mid], 2),
                    "accuracy": round(float(m["accuracy"]), 5),
                    "confidence": round(min(float(m["cycles_participated"]) / conf_n, 1.0), 3),
                    "power": vp, "delta": delta})
    audit("system", "blended_power", week, {"members": len(out), "skipped_read_fail": skipped})
    return out


def resolve_and_snapshot() -> dict:
    """POWER_MODEL=blend entrypoint: score any matured cycles, then re-snapshot
    blended voting power for everyone (humans + bots, one formula)."""
    scored = score_matured_cycles()
    snaps = snapshot_power_all()
    return {"mode": "blend", "scored_cycles": len(scored), "snapshots": len(snaps),
            "scored": scored, "power": snaps}
