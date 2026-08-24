"""Club performance from the REAL money — recorded, not re-derived.

Why this replaced the paper "decisions index" (2026-08-24, owner call):
the old series compounded the equal-weight close-to-close return of whatever
basket was decided, which is a BACKTEST of the decisions — no execution, no
cash drag, no $1-minimum granularity, no deferred buys, no partial deployment.
It printed +6.0% for a period in which the members' actual accounts did
+4.8%, -3.2% and +0.3%. A club's record has to be the money, not the idea.

Model — capital-weighted, daily time-weighted return (TWR):
  for each ET day D and each HUMAN member with a recorded value on D and on
  the previous day they have one:   r_m = v1 / v0
  club return for D = Σ(v0_m · r_m) / Σ(v0_m)     (capital-weighted)
  index(D) = index(D_prev) · club_return(D)
Chaining daily ratios is what makes joins, exits and deposits harmless: a
member only ever contributes the RETURN of money that was already in the club,
never the arrival of new money. Deposits/withdrawals are excluded outright
(member_flows), with a >25% single-day move as a backstop — a 20-name S&P
basket cannot do that, so it is a cash movement we failed to detect.

KEPT RECORD: every computed day is upserted into `club_nav` and the API reads
that table. The series is therefore a ledger that grows forward, not a number
re-invented on each request from whatever prices happen to be fetchable.
"""
from __future__ import annotations

import datetime as _dt
import time
from zoneinfo import ZoneInfo

from ..db import db

ET = ZoneInfo("America/New_York")

# A diversified 20-name basket cannot move this much in a day: treat it as an
# undetected deposit/withdrawal and drop that member-day from the chain.
_FLOW_GUARD = 0.25


def _et_date(ts: int) -> str:
    return _dt.datetime.fromtimestamp(int(ts), ET).strftime("%Y-%m-%d")


def daily_values() -> dict[str, dict[int, float]]:
    """{ET date: {member_id: last recorded account value that day}} for HUMANS.

    Bots carry seeded equity rows (a flat 100.0) and no brokerage account —
    including them would dilute every real move toward zero.
    """
    out: dict[str, dict[int, tuple[float | None, float | None]]] = {}
    with db() as conn:
        rows = conn.execute(
            "SELECT e.member_id, e.ts, e.total_value, e.club_value, "
            "       s.alloc_type, s.alloc_value "
            "FROM equity_points e "
            "JOIN members m ON m.id = e.member_id "
            "LEFT JOIN member_settings s ON s.member_id = e.member_id "
            "WHERE COALESCE(m.is_bot,0)=0 ORDER BY e.ts ASC"
        ).fetchall()
    for r in rows:
        # Prefer the RECORDED club slice (kept from 2026-08-24). Before that we
        # only have the account total, which is the slice ONLY for a member who
        # committed 100% of their account — anyone else's total carries personal
        # positions, idle cash and gifted shares that are not the club's doing.
        # (This is exactly why the old chart read +6.0%: one member's account
        # grew on a gifted share while the club's own slice was flat.)
        # Keep BOTH readings per member-day. The daily ratio must be taken from
        # the SAME field on both endpoints: club_value only began recording on
        # 2026-08-24, and silently switching basis mid-series turns a level
        # difference (idle cash, a personal position) into a phantom return —
        # observed as a +0.25% move on a Sunday when the column went live.
        total = r["total_value"]
        # An account total is the slice ONLY for a member who committed 100%.
        whole_account = (r["alloc_type"] == "percent"
                         and float(r["alloc_value"] or 0) >= 100.0)
        tv = float(total) if (total is not None and total >= 0 and whole_account) else None
        cv = float(r["club_value"]) if (r["club_value"] is not None
                                        and r["club_value"] >= 0) else None
        if tv is None and cv is None:
            continue
        out.setdefault(_et_date(r["ts"]), {})[r["member_id"]] = (tv, cv)
    return out


def flow_days() -> set[tuple[int, str]]:
    """{(member_id, ET date)} where a deposit/withdrawal was detected."""
    with db() as conn:
        try:
            rows = conn.execute("SELECT member_id, ts FROM member_flows").fetchall()
        except Exception:  # noqa: BLE001 — table predates some deployments
            return set()
    return {(r["member_id"], _et_date(r["ts"])) for r in rows}


def compute(spy_closes: dict[str, float] | None = None) -> list[dict]:
    """The chained real-money index, oldest first: [{d, club, spy, members}].

    `spy_closes` = {date: close}; when absent the benchmark column is left None
    and the club line still records (the club's own record must never depend on
    a third-party price fetch succeeding).
    """
    byday = daily_values()
    if not byday:
        return []
    flows = flow_days()
    dates = sorted(byday)
    spy_closes = spy_closes or {}
    spy_base = None
    for d in dates:
        if spy_closes.get(d):
            spy_base = spy_closes[d]
            break

    index = 100.0
    last_spy = 100.0 if spy_base else None
    prev_vals: dict[int, tuple] = dict(byday[dates[0]])
    points = [{"d": dates[0], "club": 100.0,
               "spy": 100.0 if spy_base else None,
               "members": len(prev_vals)}]
    for d in dates[1:]:
        cur = byday[d]
        num = den = 0.0
        counted = 0
        for mid, pair1 in cur.items():
            pair0 = prev_vals.get(mid)
            if pair0 is None:
                continue
            if (mid, d) in flows:
                continue                      # detected cash movement — not a return
            # Same basis on both endpoints: prefer the recorded club slice, fall
            # back to the account total only when BOTH days carry one.
            if pair0[1] is not None and pair1[1] is not None:
                v0, v1 = pair0[1], pair1[1]
            elif pair0[0] is not None and pair1[0] is not None:
                v0, v1 = pair0[0], pair1[0]
            else:
                continue
            if v0 <= 0:
                continue
            r = v1 / v0
            if abs(r - 1.0) > _FLOW_GUARD:
                continue                      # undetected cash movement
            num += v0 * r
            den += v0
            counted += 1
        if den > 0:
            index *= num / den
        # Benchmark: index the day's close, or CARRY the last known one across a
        # day we could not price (a missing close is missing information, not a
        # move). Days before the benchmark's first close stay None.
        if spy_base and spy_closes.get(d):
            spy_idx = round(100.0 * spy_closes[d] / spy_base, 4)
            last_spy = spy_idx
        else:
            spy_idx = last_spy
        points.append({"d": d, "club": round(index, 4), "spy": spy_idx,
                       "members": counted})
        # Carry members forward so a day with a missing reading doesn't break
        # the chain (the next real reading spans the gap, which is correct TWR).
        prev_vals.update(cur)
    return points


def record(points: list[dict]) -> int:
    """Upsert computed days into the kept ledger. Returns rows written."""
    if not points:
        return 0
    now = int(time.time())
    with db() as conn:
        conn.executemany(
            "INSERT INTO club_nav(d, club, spy, members, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(d) DO UPDATE SET club=excluded.club, spy=excluded.spy, "
            "members=excluded.members",
            [(p["d"], p["club"], p["spy"], p["members"], now) for p in points],
        )
    return len(points)


def series() -> dict:
    """The recorded club record, in the API's performance shape."""
    with db() as conn:
        rows = conn.execute(
            "SELECT d, club, spy, members FROM club_nav ORDER BY d ASC").fetchall()
    if not rows:
        return {"start_date": None, "computed_at": int(time.time()), "n_sessions": 0,
                "points": [], "club_pct": None, "spy_pct": None, "basis": "real_money"}
    pts = [{"d": r["d"], "club": r["club"], "spy": r["spy"]} for r in rows]
    last_spy = next((p["spy"] for p in reversed(pts) if p["spy"] is not None), None)
    return {
        "start_date": pts[0]["d"],
        "computed_at": int(time.time()),
        "n_sessions": len(pts),
        "points": pts,
        "club_pct": round(pts[-1]["club"] - 100.0, 2),
        "spy_pct": round(last_spy - 100.0, 2) if last_spy is not None else None,
        # Names the measurement so no consumer can mistake this for a backtest.
        "basis": "real_money",
    }


def _spy_closes() -> dict[str, float]:
    """{date: close} for the benchmark, best-effort via a borrowed client.

    Read-only and non-fatal: the club's own record must never depend on a
    third-party price fetch, so a failure just leaves `spy` NULL for the days
    it could not price (a later refresh fills them in).
    """
    from ..robinhood import vault
    from . import market, performance
    try:
        mid = market._any_connected_member()
        if mid is None:
            return {}
        client = vault.ensure_client(mid)
        first = min(daily_values() or {"9999-12-31": {}})
        payload = client.get_equity_historicals(["SPY"], f"{first}T00:00:00Z", interval="day")
        return performance._parse_historicals(payload).get("SPY", {})
    except Exception:  # noqa: BLE001
        return {}


def refresh() -> dict:
    """Recompute from recorded values and upsert the ledger. Never raises."""
    try:
        pts = compute(_spy_closes())
        n = record(pts)
        return {"ok": True, "days": n,
                "club_pct": round(pts[-1]["club"] - 100.0, 2) if pts else None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
