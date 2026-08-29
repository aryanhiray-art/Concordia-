"""The club's REFERENCE account — one operator-owned Alpaca account that mirrors
the decided basket, so the track record is measured instead of reconstructed.

Why it exists. The club's real-money series (services/clubnav) reads members'
Robinhood accounts, and those accounts hold personal positions, idle cash and
gifted shares alongside the club's. Robinhood does not reliably echo our
`ref_id`, so positions.club_shares_derived nets the whole order feed by symbol
and cannot tell a personal AAPL share from a club one. The consequences are on
the record: a member who joined with $1,000 uninvested was 88% of the club's
weight at +0.00% for five days, and one edit to an allocation row moved the
published record from +2.05% to +9.76%.

Here there is nothing to separate. The account holds only the club's basket,
every order carries our own `client_order_id`, and cash movements come from the
activities feed rather than a >25%-single-day-jump guess.

What it is NOT: members' returns. This account is the STRATEGY's record, run
with the operator's own money. Members will land differently — cash drag, T+1
deferrals, partial fills, different sizes. Anything published from it must say
so. It is a cash account by design: with margin it would never defer a buy and
would beat members who do, making the number unachievable rather than good.

Safety. Three independent gates (settings.alpaca_configured / reference_enabled
/ reference_armed) and a funded budget must ALL be true before a real order is
sent — settings.reference_will_place is the single place that decides. Deploying
this code cannot place an order; arming is a human action on the box.
"""
from __future__ import annotations

import datetime as _dt
import time
import uuid
from zoneinfo import ZoneInfo

from ..alpaca.client import AlpacaClient, AlpacaError
from ..config import settings
from ..db import audit, db

ET = ZoneInfo("America/New_York")
_NS = uuid.UUID("6f9d3c1e-1f2b-4a77-9a1e-5c2f7a0b4d31")   # reference-order namespace


def _et_date(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, ET).date().isoformat()


def order_tag(trade_date: str, symbol: str, side: str) -> str:
    """Deterministic client_order_id — idempotent, and attributable on the way back.

    Alpaca echoes this on the order AND in the activities feed, so a fill maps to
    the exact (cycle, symbol, side) that caused it. That round trip is the thing
    Robinhood does not make, and the reason club positions there have to be
    reconstructed by netting.
    """
    return f"concordia-{uuid.uuid5(_NS, f'{trade_date}|{symbol.upper()}|{side}')}"


# ─── planning (pure; no network, no orders) ──────────────────────────────────

def plan(symbols: list[str], budget_usd: float, held: dict[str, float] | None = None
         ) -> list[dict]:
    """Equal-weight target for `symbols` over `budget_usd` → the orders to send.

    EQUAL WEIGHT IS NOT A SIMPLIFICATION — it is what this account is for.
    services/executor.py sizes every member equally (`per_pick = budget / n_eff`,
    executor.py:614): the club's consensus weights decide WHICH names make the
    basket, and then each surviving name gets the same dollars in a member's
    account. This account measures what members actually hold, so it must size
    the same way.

    It is tempting to "fix" this by feeding in the club's vote weights, and the
    difference is not small: over 2026-06-17..08-28 the same 24 baskets return
    +8.94% equal-weighted and +3.07% vote-weighted, against SPY's +4.07%. Weighting
    by votes here would produce a number no member could have earned, which is
    the one thing this account exists to avoid. If the executor ever honours the
    weights, change it there first and this follows.

    `held` = {symbol: current market value}. Names that left the basket are sold
    in full; names in it are topped up toward the equal-weight target. A delta
    under $1 is skipped — below Alpaca's notional minimum, and churning cents
    would only add spread.
    """
    held = dict(held or {})
    out: list[dict] = []
    syms = [s.upper() for s in dict.fromkeys(symbols)]
    for s in sorted(set(held) - set(syms)):
        if held[s] > 0:
            out.append({"symbol": s, "side": "sell", "notional": round(held[s], 2),
                        "note": "exited basket"})
    if not syms or budget_usd <= 0:
        return out
    target = budget_usd / len(syms)
    for s in syms:
        delta = target - held.get(s, 0.0)
        if abs(delta) < 1.00:
            continue
        out.append({"symbol": s,
                    "side": "buy" if delta > 0 else "sell",
                    "notional": round(abs(delta), 2),
                    "note": "to equal-weight target"})
    return out


# ─── the mirror run ──────────────────────────────────────────────────────────

def run(trade_date: str, symbols: list[str], *, force_dry_run: bool = False) -> dict:
    """Mirror one decided basket into the reference account.

    Returns a summary in the executor's shape. `will_place` is False unless every
    gate is on; a dry run plans and returns exactly what it WOULD have sent.
    """
    will_place = settings.reference_will_place and not force_dry_run
    summary: dict = {"trade_date": trade_date, "will_place": will_place,
                     "paper": settings.alpaca_paper, "orders": [], "note": None}
    if not settings.alpaca_configured:
        summary["note"] = "no Alpaca key pair configured"
        return summary
    try:
        client = AlpacaClient()
        acct = client.account()
        held = {p["symbol"].upper(): float(p.get("market_value") or 0.0)
                for p in client.positions()}
    except AlpacaError as e:
        summary["note"] = f"{type(e).__name__}: {e}"
        return summary

    budget = settings.reference_budget_usd or float(acct.get("equity") or 0.0)
    summary["equity"] = acct.get("equity")
    summary["budget_usd"] = round(budget, 2)
    orders = plan(symbols, budget, held)

    for o in orders:
        row = dict(o, status="reviewed")
        if will_place:
            try:
                placed = client.submit_order(
                    o["symbol"], notional=o["notional"], side=o["side"],
                    client_order_id=order_tag(trade_date, o["symbol"], o["side"]))
                row["status"] = "placed"
                row["order_id"] = placed.get("id")
            except AlpacaError as e:
                row["status"] = "error"
                row["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        summary["orders"].append(row)

    if not will_place:
        summary["note"] = ("review only — reference_will_place is False "
                           f"(configured={settings.alpaca_configured}, "
                           f"enabled={settings.reference_enabled}, "
                           f"armed={settings.reference_armed}, "
                           f"budget={settings.reference_budget_usd})")
    audit("system", "reference_run", trade_date,
          {"will_place": will_place, "orders": len(summary["orders"]),
           "paper": settings.alpaca_paper})
    return summary


# ─── the record ──────────────────────────────────────────────────────────────

def _net_flows(client: AlpacaClient, since: str) -> dict[str, float]:
    """{ET date: net cash moved} from the activities feed — real, not inferred."""
    flows: dict[str, float] = {}
    for kind, sign in (("CSD", 1.0), ("CSW", -1.0), ("JNLC", 1.0)):
        try:
            rows = client.activities(kind, after=f"{since}T00:00:00Z")
        except AlpacaError:
            continue
        for a in rows:
            when = a.get("date") or a.get("transaction_time") or a.get("created_at")
            amt = a.get("net_amount") if a.get("net_amount") is not None else a.get("amount")
            if not when or amt is None:
                continue
            try:
                v = float(amt)
            except (TypeError, ValueError):
                continue
            d = str(when)[:10]
            flows[d] = flows.get(d, 0.0) + (abs(v) * sign if kind != "JNLC" else v)
    return flows


def snapshot() -> dict:
    """Pull the account's equity curve + cash flows and upsert reference_nav."""
    if not settings.alpaca_configured:
        return {"ok": False, "error": "not configured"}
    try:
        client = AlpacaClient()
        hist = client.portfolio_history(period="all", timeframe="1D")
    except AlpacaError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ts = hist.get("timestamp") or []
    eq = hist.get("equity") or []
    rows = [(_et_date(t), float(v)) for t, v in zip(ts, eq) if v is not None and float(v) > 0]
    if not rows:
        return {"ok": True, "days": 0, "note": "account has no equity history yet"}

    first = rows[0][0]
    flows = _net_flows(client, first)
    try:
        spy = client.daily_closes("SPY", f"{first}T00:00:00Z")
    except AlpacaError:
        spy = {}

    now = int(time.time())
    with db() as conn:
        conn.executemany(
            "INSERT INTO reference_nav(d,equity,net_flow,spy_close,created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(d) DO UPDATE SET equity=excluded.equity, "
            "net_flow=excluded.net_flow, "
            "spy_close=COALESCE(excluded.spy_close, reference_nav.spy_close)",
            [(d, v, flows.get(d, 0.0), spy.get(d), now) for d, v in rows],
        )
    return {"ok": True, "days": len(rows), "start": first, "end": rows[-1][0]}


def series() -> dict:
    """Time-weighted index vs SPY, computed at read time from raw observations.

    Each day's return removes that day's cash flow before dividing, which is what
    makes it time-weighted: funding the account is not performance. clubnav has
    to approximate this by discarding any day that moved more than 25%.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT d, equity, net_flow, spy_close FROM reference_nav ORDER BY d ASC"
        ).fetchall()
    empty = {"start_date": None, "computed_at": int(time.time()), "n_sessions": 0,
             "points": [], "club_pct": None, "spy_pct": None, "basis": "reference_account"}
    if not rows:
        return empty
    spy_base = next((r["spy_close"] for r in rows if r["spy_close"]), None)
    idx, last_spy = 100.0, None
    points = [{"d": rows[0]["d"], "club": 100.0, "spy": 100.0 if spy_base else None}]
    for prev, cur in zip(rows, rows[1:]):
        v0 = float(prev["equity"] or 0.0)
        if v0 > 0:
            r = (float(cur["equity"] or 0.0) - float(cur["net_flow"] or 0.0)) / v0
            if r > 0:
                idx *= r
        if spy_base and cur["spy_close"]:
            last_spy = round(100.0 * float(cur["spy_close"]) / float(spy_base), 4)
        points.append({"d": cur["d"], "club": round(idx, 4), "spy": last_spy})
    return {"start_date": points[0]["d"], "computed_at": int(time.time()),
            "n_sessions": len(points), "points": points,
            "club_pct": round(idx - 100.0, 2),
            "spy_pct": round(last_spy - 100.0, 2) if last_spy is not None else None,
            "basis": "reference_account"}


def status() -> dict:
    """Everything the admin panel needs to explain the account's state."""
    from ..alpaca.client import probe
    s = series()
    return {"configured": settings.alpaca_configured,
            "paper": settings.alpaca_paper,
            "enabled": settings.reference_enabled,
            "armed": settings.reference_armed,
            "budget_usd": settings.reference_budget_usd,
            "will_place": settings.reference_will_place,
            "probe": probe(),
            "days_recorded": s["n_sessions"],
            "start_date": s["start_date"],
            "club_pct": s["club_pct"],
            "spy_pct": s["spy_pct"]}
