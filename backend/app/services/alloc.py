"""Per-member allocation, agentic-account balance, and club-only P&L.

A member commits a slice of their Robinhood account to the club — either a
percentage of buying power or a fixed dollar amount. That committed amount is
their stake (= voting power) and the budget the executor deploys. Profit is
tracked ONLY on what the club actually bought (from placed executions), never
the member's whole account.
"""
from __future__ import annotations

import json
import time

from ..config import settings
from ..db import db, kv_get, kv_set
from ..robinhood import vault

_READY_TTL = 180  # seconds to cache a trade-readiness probe (avoids RH rate limits)


def trade_readiness(member_id: int, force: bool = False) -> dict:
    """Can this member's account actually place a buy right now?

    Robinhood blocks buys on an agentic account that hasn't completed its
    investing-goals/suitability questionnaire — the review returns an
    EQUITY_SUITABILITY alert and the place 400s. We probe with a tiny $1 review
    (no order placed) and cache the verdict so onboarding can guide the member.

    A no-idle-cash alert (EQUITY_NOT_ENOUGH_BP*) counts as READY: fully
    invested is the executor's steady state (sell → settle → redeploy leaves
    only the reserve), so treating it as a blocker failed every active member
    forever.
    """
    acct = vault.account_number_for(member_id)
    if not acct:
        return {"connected": False, "ready": False, "reason": "not_connected"}
    ck = f"trade_ready:{member_id}"
    if not force:
        cached = kv_get(ck)
        if cached:
            try:
                c = json.loads(cached)
                if time.time() - c.get("ts", 0) < _READY_TTL:
                    return {k: v for k, v in c.items() if k != "ts"}
            except Exception:  # noqa: BLE001
                pass
    out = {"connected": True, "ready": False, "reason": "unknown"}
    try:
        client = vault.ensure_client(member_id)
        rv = client.review_market_buy(acct, "AAPL", "1.00")
        checks = (rv.get("data") or rv).get("order_checks")
        atype = checks.get("alertType") if isinstance(checks, dict) else None
        if atype == "EQUITY_SUITABILITY":
            out = {"connected": True, "ready": False, "reason": "needs_questionnaire"}
        elif isinstance(atype, str) and atype.startswith("EQUITY_NOT_ENOUGH_BP"):
            # Under $1 of idle cash — not a broken account. The probe has already
            # proven what it exists to prove (token good, questionnaire done, RH
            # evaluated the order and only complained about cash), and the
            # executor deliberately keeps active members spent down below the
            # probe amount.
            out = {"connected": True, "ready": True, "reason": "fully_invested"}
        elif checks:
            out = {"connected": True, "ready": False, "reason": "blocked", "detail": str(checks)[:140]}
        else:
            out = {"connected": True, "ready": True, "reason": "ok"}
    except Exception as e:  # noqa: BLE001
        out = {"connected": True, "ready": False, "reason": "probe_failed", "detail": str(e)[:140]}
    kv_set(ck, json.dumps({**out, "ts": int(time.time())}))
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def member_balance(member_id: int) -> dict | None:
    """Live agentic-account balance, or None if not connected/reachable."""
    acct = vault.account_number_for(member_id)
    if not acct:
        return None
    try:
        client = vault.ensure_client(member_id)
        port = (client.get_portfolio(acct).get("data") or {})
    except Exception:  # noqa: BLE001
        return None
    bp = port.get("buying_power") or {}
    return {
        "total_value": _num(port.get("total_value")),
        "cash": _num(port.get("cash")),
        "buying_power": _num(bp.get("buying_power") if isinstance(bp, dict) else bp),
    }


def resolve_allocation(alloc_type: str | None, alloc_value: float | None,
                       buying_power: float | None) -> float:
    """The committed $ for this member: % of buying power, or a fixed $, capped at BP."""
    bp = buying_power or 0.0
    v = alloc_value or 0.0
    if alloc_type == "dollar":
        amt = v
    else:  # percent (default)
        amt = bp * (v / 100.0)
    return round(max(0.0, min(amt, bp)), 2)


def rebalance_budget(alloc_type: str | None, alloc_value: float | None,
                     total_value: float | None, buying_power: float | None,
                     daily_cap_usd: float | None = None) -> float:
    """Target $ of the basket to HOLD — the committed stake, not free cash.

    Unlike resolve_allocation (which caps at buying power for an initial buy), the
    rebalance frees cash by selling, so the target must be the full committed
    stake even when it's already deployed in positions. For a $ stake that's the
    committed dollars (capped at total account value); for % it's % of equity.

    Hard caps (safety, always applied): the global MAX_NOTIONAL_PER_MEMBER
    ceiling from config, and — when the caller passes the member's
    `daily_cap_usd` (>0 means set; None/0 means no cap) — that per-member cap.
    """
    tv = total_value or 0.0
    v = alloc_value or 0.0
    if alloc_type == "dollar":
        target = min(v, tv)
    else:  # percent (default)
        target = tv * (v / 100.0)
    target = min(target, settings.max_notional_per_member)
    if daily_cap_usd is not None and daily_cap_usd > 0:
        target = min(target, daily_cap_usd)
    return round(max(0.0, target), 2)


def member_allocation(member_id: int, club_value: float | None = None) -> dict:
    """Current allocation setting + the live $ it resolves to (needs a fresh balance).

    For a COMPOUNDING dollar stake the honest figure is what the executor will
    actually deploy — the grown/shrunk slice — not the committed base. Callers
    that already know the member's club position value (accountsnap) hand it in;
    without it the pinned figure is shown rather than paying for a second read.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT alloc_type, alloc_value, COALESCE(compound,1) AS compound, alloc_applied,"
            "       COALESCE(slice_carry,0) AS slice_carry "
            "FROM member_settings WHERE member_id=?", (member_id,)
        ).fetchone()
    bal = member_balance(member_id)
    bp = (bal or {}).get("buying_power")
    tv = (bal or {}).get("total_value")
    atype = (row["alloc_type"] if row else "percent")
    aval = (row["alloc_value"] if row else 0.0)
    return {
        "type": atype,
        "value": aval,
        # Resolve against TOTAL account value via rebalance_budget — the same
        # figure the executor + voting power use — NOT resolve_allocation, which
        # caps at buying power and made a deployed $100 stake read as ~$5 on the
        # dashboard while voting power correctly showed ~$100 (finding #18).
        "resolved_usd": (
            compound_budget(aval, row["alloc_applied"], club_value, tv,
                            slice_carry=row["slice_carry"])
            if (row is not None and atype == "dollar" and int(row["compound"] or 0) == 1
                and club_value is not None)
            else rebalance_budget(atype, aval, tv, bp)
        ),
        "compounding": bool(row is not None and atype == "dollar"
                            and int(row["compound"] or 0) == 1),
        "balance": bal,
    }


def club_pnl(member_id: int, holds: list[dict] | None = None) -> dict:
    """Unrealized P&L on the member's LIVE holdings — current value vs the broker's
    average cost basis, read straight from the account.

    Source = positions.holdings_with_value (the actual account), NOT the
    reconstructed club_shares ledger: a member's MANUAL sale isn't recorded in the
    ledger (reconcile only sees the club's own agentic orders), so after a manual
    liquidate-then-rebuy the ledger double-counts and reports phantom positions /
    2x invested. The account is the truth, so value + position count + cost basis
    all come from it. (finding #7 + the manual-sale ledger-drift it exposed.)

    `holds` lets a caller that has ALREADY read the member's positions hand them
    in (services/accountsnap.build), so building a snapshot costs ONE holdings
    read instead of two. Omitted/None = read them here, i.e. behaviour identical
    to before for every existing caller.
    """
    from . import positions
    if holds is None:
        holds = positions.holdings_with_value(member_id)   # live positions, valued vs avg cost
    if not holds:
        return {"invested": 0.0, "value": 0.0, "profit": 0.0, "profit_pct": None, "positions": 0}
    value = sum(h.get("value") or 0.0 for h in holds)
    profit = sum(h["pnl"] for h in holds if h.get("pnl") is not None)
    invested = value - profit          # cost basis = current value − unrealized gain
    return {
        "invested": round(invested, 2),
        "value": round(value, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit / invested * 100, 2) if invested else None,
        "positions": len(holds),
    }


# ─── compounding stake (owner decision 2026-08-24, default ON) ──────────────

def compound_budget(alloc_value: float | None, alloc_applied: float | None,
                    club_value: float | None, total_value: float | None,
                    daily_cap_usd: float | None = None,
                    slice_carry: float | None = None) -> float:
    """Rebalance target for a COMPOUNDING dollar stake.

    The slice rides its own P&L in BOTH directions from the committed base:
    grown to $110 → deploy $110; fallen to $90 → deploy $90 (never topped up
    from fresh cash). `alloc_applied` is the stake figure the executor last
    honored, so a stake CHANGE deploys/retires exactly the delta:

        budget = club_value + (alloc_value − alloc_applied)

    `slice_carry` is the club money the LAST cycle intended to deploy but
    could not (buys deferred for settled cash, an order-cap skip). Without it
    the positions-only mark is a one-way downward ratchet: undeployed dollars
    sit as cash, the next mark cannot see them, and they leave the slice
    forever. It is recomputed fresh each buy leg (never accumulated) and is
    always club money we ourselves declined to spend — never the member's
    personal cash, so a market LOSS is still never topped up.

    An empty slice (< $1 held) bootstraps from the full stake — first joiners,
    liquidated accounts, and pre-feature members (alloc_applied NULL) all start
    by deploying alloc_value. Safety clamps unchanged: total account value,
    MAX_NOTIONAL_PER_MEMBER, and the member's own daily cap.
    """
    v = alloc_value or 0.0
    cv = (club_value or 0.0) + max(0.0, slice_carry or 0.0)
    if cv < 1.00:
        target = v
    else:
        applied = v if alloc_applied is None else (alloc_applied or 0.0)
        target = cv + (v - applied)
    target = min(target, total_value or 0.0, settings.max_notional_per_member)
    if daily_cap_usd is not None and daily_cap_usd > 0:
        target = min(target, daily_cap_usd)
    return round(max(0.0, target), 2)


def basket_targets(budget: float, held_vals: dict[str, float],
                   band: float = 1.00) -> dict[str, float]:
    """Per-name dollar targets that SUM to ~budget (water-fill).

    Fixes the old equal-weight dead-band drift: a name that has grown past its
    fair share but by less than `band` (the broker's $1 order minimum makes a
    smaller trim unplaceable) KEEPS its value, and the remaining budget spreads
    equally over the other names — so winners ride and the rest shrink to make
    the total add up. A name more than `band` over its fair share is targeted
    AT fair share (the executor's sell leg trims it).

    The aggregate can exceed budget by at most ~band per kept name in the
    degenerate everyone-at-the-line case — the unavoidable cost of a $1
    minimum order, not a sizing error.
    """
    names = list(held_vals)
    if not names:
        return {}
    if budget <= 0:
        return {n: 0.0 for n in names}
    kept: dict[str, float] = {}
    rest = set(names)
    rem = float(budget)
    while rest:
        fair = rem / len(rest)
        # names over fair share but within the band: pin at their held value
        pin = [n for n in rest if fair < held_vals[n] <= fair + band]
        if not pin:
            break
        pinned_total = sum(held_vals[n] for n in pin)
        others = len(rest) - len(pin)
        # A pin must never be PAID FOR by starving the laggards: if keeping
        # these winners leaves the remaining names under the $1 broker minimum
        # (or overshoots the budget outright), stop pinning and let everyone
        # take fair share — the executor then trims the overweight names
        # instead of liquidating the underweight one.
        if others and (rem - pinned_total) / others < 1.00:
            break
        if not others and pinned_total > rem:
            break
        for n in pin:
            kept[n] = held_vals[n]
            rem -= held_vals[n]
            rest.discard(n)
    fair = (rem / len(rest)) if rest else 0.0
    for n in rest:
        kept[n] = max(0.0, fair)
    return {n: round(v, 2) for n, v in kept.items()}
