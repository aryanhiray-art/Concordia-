"""EMERGENCY TRADE FIX — exit one stranded position, now, outside the cycle.

Why this exists. On 2026-08-31 two members held VMRK, a name the club had voted
OUT weeks earlier, and the executor could not see it: the broker's order feed
still called it AVB while the ledger had been renamed to VMRK, so the derived
club position dropped it from both sides. Verification failed every cycle with
"dropped from basket but still held", and there was NO way to act on it — the
only path to a sell was the weekly executor, which was the very thing that could
not see the position. The club was stuck holding a name nobody had chosen, with
no lever.

This is that lever. It is deliberately narrow:

  * ONE member, ONE symbol, one full exit. It cannot buy, cannot resize, cannot
    touch a basket name, and cannot be pointed at the whole club.
  * It sells only what the CLUB can prove it owns for that member — the derived
    club position, or the ledger, whichever the instance is configured for,
    intersected with what the account actually holds. A member's personal
    shares are unreachable by construction, exactly as in the executor.
  * It REFUSES to sell a name in the current basket. If the club voted for it,
    the cycle owns it; a manual exit would be overridden the next run anyway.
  * dry_run is the default and returns the full plan.
  * It writes a normal `executions` row, so the ledger, verification and every
    downstream reconciliation see it as an ordinary club sell — not a ghost.

It intentionally does NOT bypass the arm/live gates. If the club is disarmed or
in dry-run, this plans and reports but places nothing: an emergency lever that
ignores the kill switch is not a safety feature, it is a second way to lose
control of the account.
"""
from __future__ import annotations

import time
import uuid

from ..config import settings
from ..db import audit, db, is_armed, kv_get
from . import executor
from ..robinhood import vault
from . import positions, voting

# Same namespace the executor uses, so a rescue sell is idempotent per
# (member, symbol, day) and a double-click cannot double-sell.
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _ref_id(member_id: int, symbol: str) -> str:
    day = time.strftime("%Y-%m-%d")
    return str(uuid.uuid5(_NS, f"RESCUE:{day}:{member_id}:{symbol}"))


def plan(member_id: int, symbol: str) -> dict:
    """What a rescue sell WOULD do. Never places. Never raises on a live read."""
    symbol = (symbol or "").strip().upper()
    out: dict = {"member_id": member_id, "symbol": symbol, "ok": False}

    session = voting.latest_session("executed") or voting.latest_session("closed")
    basket = {p["symbol"] for p in (voting.session_picks(session) or [])} if session else set()
    if symbol in basket:
        out["reason"] = (f"{symbol} is in the current basket — the cycle owns it. "
                         "A manual exit would be re-bought on the next run.")
        return out

    acct = vault.account_number_for(member_id)
    if not acct:
        out["reason"] = "member has no connected brokerage account"
        return out
    try:
        client = vault.ensure_client(member_id)
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"broker unreachable: {type(e).__name__}: {e}"
        return out

    # What the CLUB owns — the same source the executor trusts, so this can
    # never reach a member's personal shares.
    try:
        if settings.club_position_source == "derived":
            club = positions.club_shares_derived(member_id, client=client, account_number=acct)
        else:
            club = positions.club_shares(member_id)
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"could not establish the club position (fail closed): {e}"
        return out
    club_qty = float(club.get(symbol) or 0.0)

    live = {h["symbol"]: float(h["quantity"] or 0.0)
            for h in positions.holdings_with_value(member_id)}
    live_qty = live.get(symbol, 0.0)

    qty = min(club_qty, live_qty)
    out.update({"club_qty": round(club_qty, 6), "account_qty": round(live_qty, 6),
                "sell_qty": round(qty, 6), "in_basket": False,
                "armed": is_armed(), "is_live": settings.is_live,
                "halted": kv_get("halt") == "1"})
    if club_qty <= 1e-9:
        out["reason"] = (f"the club holds no {symbol} for this member — refusing. "
                         "(If the account holds it, it is a personal position.)")
        return out
    if live_qty <= 1e-9:
        out["reason"] = f"the account no longer holds {symbol} — nothing to sell"
        return out
    out["ok"] = True
    out["reason"] = f"would sell {qty:.6f} {symbol} (full club exit)"
    return out


def _attach_session() -> int | None:
    """executions.session_id is NOT NULL, so a rescue row is filed against the
    latest decided cycle. It is still identifiable as a rescue by its ref_id
    prefix and place_json._rescue, and reconciliation treats it as any club sell."""
    s = voting.latest_session("executed") or voting.latest_session("closed")
    return s["id"] if s else None


def rescue_sell(member_id: int, symbol: str, actor: str, dry_run: bool = True) -> dict:
    """Plan, then (unless dry_run) place a single full club exit."""
    p = plan(member_id, symbol)
    p["dry_run"] = dry_run
    audit(actor, "rescue_sell_planned", f"{member_id}:{symbol}", p)
    if not p.get("ok") or dry_run:
        return p
    if not (settings.is_live and is_armed()) or kv_get("halt") == "1":
        p["placed"] = False
        p["reason"] = ("club is not live+armed (or is halted) — planned only. "
                       "This lever deliberately does not bypass the kill switch.")
        return p

    symbol = symbol.strip().upper()
    acct = vault.account_number_for(member_id)
    client = vault.ensure_client(member_id)
    # 6dp via the executor's own float-safe floor — the raw int(q*1e6) form
    # this used had the crumb-minting truncation bug (0.00399 → 0.003989),
    # and a residue below the broker's sell minimum must not be ordered at
    # all (a month of Charlie's rejected 1e-6 ALL sells, 2026-09-10).
    qty = executor.floor_qty6(p["sell_qty"])
    if 0 < qty < executor.MIN_SELL_QTY:
        p["placed"] = False
        p["reason"] = "residue below broker sell minimum — left in place"
        return p
    ref = _ref_id(member_id, symbol)
    with db() as conn:
        if conn.execute("SELECT 1 FROM executions WHERE ref_id=?", (ref,)).fetchone():
            p["placed"] = False
            p["reason"] = "already rescued today (idempotent)"
            return p
    try:
        res = client.place_market_sell(acct, symbol, f"{qty:.6f}", ref_id=ref)
    except Exception as e:  # noqa: BLE001
        with db() as conn:
            conn.execute(
                "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
                "ref_id,status,error,created_at) VALUES(?,?,?,'sell','market',NULL,?,'error',?,?)",
                (_attach_session(), member_id, symbol, ref, f"rescue: {e}", int(time.time())))
        p.update({"placed": False, "error": str(e)})
        audit(actor, "rescue_sell_error", f"{member_id}:{symbol}", {"err": str(e)})
        return p
    place = dict(res) if isinstance(res, dict) else {"raw": str(res)}
    place["_concord_est_qty"] = qty
    place["_rescue"] = True
    import json as _json
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
            "ref_id,status,place_json,created_at) "
            "VALUES(?,?,?,'sell','market',NULL,?,'placed',?,?)",
            (_attach_session(), member_id, symbol, ref, _json.dumps(place), int(time.time())))
    p.update({"placed": True, "qty": qty})
    audit(actor, "rescue_sell_placed", f"{member_id}:{symbol}", {"qty": qty, "ref_id": ref})
    return p


def stranded() -> list[dict]:
    """Every (member, symbol) the club holds that is NOT in the current basket.

    The list this lever is for. Read-only and best-effort per member: one
    unreachable account must not hide everyone else's stranded names.
    """
    session = voting.latest_session("executed") or voting.latest_session("closed")
    basket = {p["symbol"] for p in (voting.session_picks(session) or [])} if session else set()
    from . import membership
    with db() as conn:
        mids = [r["id"] for r in conn.execute(
            "SELECT m.id FROM members m JOIN rh_accounts a ON a.member_id=m.id "
            f"WHERE COALESCE(m.is_bot,0)=0 AND a.status='connected' AND {membership.active_clause('m')}"
        ).fetchall()]
    out: list[dict] = []
    for mid in mids:
        try:
            acct = vault.account_number_for(mid)
            client = vault.ensure_client(mid)
            club = (positions.club_shares_derived(mid, client=client, account_number=acct)
                    if settings.club_position_source == "derived" else positions.club_shares(mid))
            live = {h["symbol"]: h for h in positions.holdings_with_value(mid)}
        except Exception as e:  # noqa: BLE001
            out.append({"member_id": mid, "error": f"{type(e).__name__}: {e}"})
            continue
        for sym, q in club.items():
            if sym in basket or q <= 1e-9:
                continue
            h = live.get(sym)
            out.append({"member_id": mid, "symbol": sym, "club_qty": round(q, 6),
                        "account_qty": round(float(h["quantity"]), 6) if h else 0.0,
                        "value": round(float(h["value"]), 2) if h else 0.0})
    return out
