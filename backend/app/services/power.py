"""Voting power = each member's account equity ($), the weight on their votes.

Snapshotted when a member connects Robinhood and re-allocated every Sunday (the
weekly job). Week-over-week change is the "increase/decrease" members and the
owner see. History lives in voting_power_history.
"""
from __future__ import annotations

import datetime as _dt
import time

from ..calendar_us import now_et
from ..db import audit, db
from ..robinhood import vault
from . import alloc


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def week_start_et(d: _dt.date | None = None) -> str:
    """The Sunday (ET) that anchors the given date's week → 'YYYY-MM-DD'."""
    d = d or now_et().date()
    return (d - _dt.timedelta(days=(d.weekday() + 1) % 7)).isoformat()


def member_equity(member_id: int) -> float | None:
    """Live account equity ($) from the member's Robinhood portfolio, or None."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return None
    try:
        client = vault.ensure_client(member_id)
        port = client.get_portfolio(acct)
    except Exception:  # noqa: BLE001 — unreachable / revoked
        return None
    data = port.get("data", port) if isinstance(port, dict) else {}
    if not isinstance(data, dict):
        return None
    return _num(data.get("total_value")) if _num(data.get("total_value")) is not None \
        else _num(data.get("equity_value"))


def _set_current(member_id: int, power: float) -> None:
    with db() as conn:
        conn.execute("UPDATE member_settings SET voting_power=?, updated_at=? WHERE member_id=?",
                     (power, int(time.time()), member_id))


def current_power(member_id: int) -> float:
    with db() as conn:
        row = conn.execute("SELECT voting_power FROM member_settings WHERE member_id=?",
                           (member_id,)).fetchone()
    return float(row["voting_power"]) if row else 0.0


def _prev_power(member_id: int, before_week: str) -> float | None:
    with db() as conn:
        row = conn.execute(
            "SELECT power FROM voting_power_history WHERE member_id=? AND week_start<? "
            "ORDER BY week_start DESC LIMIT 1", (member_id, before_week)
        ).fetchone()
    return float(row["power"]) if row else None


def snapshot_member(member_id: int, week: str | None = None) -> dict | None:
    """Snapshot a member's committed allocation → voting power + history. Returns the row.

    Voting power = the $ the member commits to the club (their stake), not their
    whole account. Re-run on connect, on allocation change, and every Sunday.

    The stake is measured against TOTAL account value (alloc.rebalance_budget —
    the same sizing the executor's budget trusts), NOT against idle buying
    power: once the stake is deployed into positions, buying power collapses to
    loose cash and would wrongly crater the member's power (the $100-stake →
    $5.20-power bug). resolve_allocation stays as-is for initial-buy sizing.
    """
    # Under POWER_MODEL=blend this function is the WRONG formula, and it is
    # called unconditionally on connect and on every allocation change. It writes
    # raw committed stake as voting power, so a member who connected saw their
    # power equal their dollars — then the next scheduled blended snapshot
    # recomputed it properly and the UI rendered the difference as a loss they
    # had caused. (Observed: a $1,000 member stamped 1000.00 on connect, then
    # 650.13 four days later with a -349.87 delta, having done nothing.) Under
    # blend, the club-wide snapshot owns the number; do not write a second one.
    from ..config import settings as _settings
    if _settings.power_model == "blend":
        # SEED the member's dollar weight, but write NO history row.
        #
        # The history row is what rendered the phantom loss (the delta column),
        # so it must not be written here. But simply returning was worse: this
        # is the ONLY writer on connect and on an allocation change, and the
        # club-wide snapshot runs on a biweekly cadence — so a member who
        # committed $2,000 sat at voting._tally_rows' MAX(voting_power, 1.0)
        # floor for up to 13 days, voting at 0.99% of the club instead of ~95%,
        # through live executions, while their own UI showed $0. A cosmetic bug
        # is not worth disenfranchising the people who just funded the club.
        #
        # Seeding the committed stake is the right interim value: under the
        # merit form cold start IS raw stake, so this is what they would be
        # awarded anyway, and the next scheduled snapshot normalises it against
        # everyone else's multiplier.
        bal = alloc.member_balance(member_id)
        if bal is None:
            return None
        with db() as conn:
            row = conn.execute(
                "SELECT alloc_type, alloc_value FROM member_settings WHERE member_id=?",
                (member_id,)).fetchone()
        stake = alloc.rebalance_budget(
            row["alloc_type"] if row else "percent", row["alloc_value"] if row else 0.0,
            bal.get("total_value"), bal.get("buying_power")) or 0.0
        stake = round(max(0.0, stake), 2)
        with db() as conn:
            conn.execute("UPDATE member_settings SET voting_power=?, last_capital_usd=?, "
                         "updated_at=? WHERE member_id=?",
                         (stake, stake, int(time.time()), member_id))
        return {"member_id": member_id, "week_start": week or week_start_et(),
                "power": stake, "equity": bal.get("total_value"),
                "prev_power": None, "delta": None, "seeded": True}
    week = week or week_start_et()
    bal = alloc.member_balance(member_id)
    if bal is None:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT alloc_type, alloc_value FROM member_settings WHERE member_id=?", (member_id,)
        ).fetchone()
    power = alloc.rebalance_budget(
        row["alloc_type"] if row else "percent",
        row["alloc_value"] if row else 0.0,
        bal.get("total_value"),
        bal.get("buying_power"),
    )
    equity = bal.get("total_value")
    prev = _prev_power(member_id, week)
    delta = (power - prev) if prev is not None else None
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO voting_power_history(member_id,week_start,power,equity,prev_power,delta,created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(member_id,week_start) DO UPDATE SET "
            "power=excluded.power, equity=excluded.equity, prev_power=excluded.prev_power, delta=excluded.delta",
            (member_id, week, power, equity, prev, delta, now),
        )
    _set_current(member_id, power)
    return {"member_id": member_id, "week_start": week, "power": power,
            "equity": equity, "prev_power": prev, "delta": delta}


def snapshot_all() -> list[dict]:
    """Re-allocate voting power for every member (the Sunday job).

    POWER_MODEL=blend delegates to the ETHGlobal 50/50 capital+accuracy model
    (reputation.resolve_and_snapshot: score matured cycles, then snapshot everyone,
    humans + bots, one formula). Legacy 'stake' mode = committed stake per member.
    """
    from ..config import settings
    if settings.power_model == "blend":
        from . import reputation
        res = reputation.resolve_and_snapshot()
        return [{"member_id": s["member_id"], "week_start": week_start_et(), "power": s["power"],
                 "equity": s["capital"], "prev_power": None, "delta": s["delta"]}
                for s in res["power"]]
    week = week_start_et()
    with db() as conn:
        ids = [r["member_id"] for r in conn.execute(
            "SELECT member_id FROM rh_accounts WHERE status='connected'").fetchall()]
    out = []
    for mid in ids:
        r = snapshot_member(mid, week)
        if r:
            out.append(r)
    audit("system", "weekly_power", week, {"members": len(out)})
    return out


def history(member_id: int, limit: int = 26) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT week_start, power, equity, prev_power, delta FROM voting_power_history "
            "WHERE member_id=? ORDER BY week_start DESC LIMIT ?", (member_id, limit)
        ).fetchall()
    return [dict(r) for r in rows][::-1]   # oldest→newest for charting


def member_power_view(member_id: int) -> dict:
    """Current power + latest delta + history, for the member's own UI."""
    h = history(member_id)
    latest = h[-1] if h else None
    return {
        "power": current_power(member_id),
        "delta": (latest or {}).get("delta"),
        "as_of_week": (latest or {}).get("week_start"),
        "history": h,
    }
