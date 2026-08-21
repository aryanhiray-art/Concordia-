"""Ticker renames / corporate actions — detect and repair ledger↔account drift.

The 2026-08 incident this exists for: the club bought AvalonBay (AVB) on 08/14;
days later the company became Vivmark Residential (VMRK) and Robinhood swapped
the shares in place. The club ledger (reconstructed from `executions`) kept
saying AVB — a phantom the account no longer held — while the very real VMRK
position looked "personal" and could never be sold (sells come only from
ledger ∩ account). Two layers here:

  detect()  — DB-only pairing of ledger phantoms with unledgered account
              positions, per member. A pair the SAME member exhibits on both
              sides (exactly one phantom + exactly one stray, so there is no
              ambiguity) is a probable rename; corroboration = how many
              members show the identical old→new pair. Cheap enough to ride
              in the admin panel payload.
  migrate() — the owner-confirmed repair: rename the execution rows, then
              TRUE UP each member's ledger quantity to the account with an
              append-only $0 adjustment row (renames rarely convert 1:1 —
              AVB→VMRK was ~2.8:1 — and the ledger's estimated shares must
              match reality or a later full exit strands the difference).
              Also swaps the universe row so the new ticker is votable.
              History is never rewritten beyond the symbol column; the
              adjustment is a new, clearly-labeled row.
"""
from __future__ import annotations

import json
import time

from ..db import audit, db
from . import positions

_DUST_SH = 1e-6
_DUST_USD = 0.25   # ignore sub-quarter strays (real dust, promo crumbs)


def _live_holdings(conn, member_id: int) -> dict[str, dict]:
    row = conn.execute(
        "SELECT holdings FROM account_snapshots WHERE member_id=?", (member_id,)
    ).fetchone()
    if not row or not row["holdings"]:
        return {}
    try:
        return {h["symbol"]: h for h in json.loads(row["holdings"]) if h.get("symbol")}
    except Exception:  # noqa: BLE001
        return {}


def detect() -> list[dict]:
    """[{old, new, members: [names], live_value}] — probable ticker renames.

    Only unambiguous per-member pairs count (exactly one ledger phantom AND
    exactly one unledgered position), so a member's personal stock picks never
    pair with an unrelated phantom.
    """
    with db() as conn:
        members = conn.execute(
            "SELECT m.id, m.display_name FROM members m "
            "JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE COALESCE(m.is_bot,0)=0 AND a.status='connected'").fetchall()
        pairs: dict[tuple[str, str], dict] = {}
        for m in members:
            live = _live_holdings(conn, m["id"])
            if not live:
                continue
            ledger = {s: q for s, q in positions.club_shares(m["id"]).items() if q > _DUST_SH}
            phantoms = [s for s in ledger if s not in live]
            strays = [s for s, h in live.items()
                      if s not in ledger and float(h.get("value") or 0) > _DUST_USD]
            if len(phantoms) == 1 and len(strays) == 1:
                key = (phantoms[0], strays[0])
                agg = pairs.setdefault(key, {"old": key[0], "new": key[1],
                                             "members": [], "live_value": 0.0})
                agg["members"].append(m["display_name"])
                agg["live_value"] += float(live[key[1]].get("value") or 0)
    out = sorted(pairs.values(), key=lambda p: -len(p["members"]))
    for p in out:
        p["live_value"] = round(p["live_value"], 2)
    return out


def migrate(old: str, new: str, name: str | None = None, actor: str = "admin") -> dict:
    """Repair the ledger after a ticker change. Idempotent per (old, new).

    1. executions.symbol old→new (every member — a rename is global).
    2. Per member: append a $0 'corporate action' adjustment row so the
       ledger's share count equals the account's actual position.
    3. Universe: deactivate old, upsert new as active (votable — the renamed
       company keeps its index membership unless the owner says otherwise).
    """
    old, new = old.strip().upper(), new.strip().upper()
    if not old or not new or old == new:
        raise ValueError("need two different symbols")
    now = int(time.time())
    with db() as conn:
        renamed = conn.execute(
            "UPDATE executions SET symbol=? WHERE symbol=?", (new, old)).rowcount
        # a session id for the NOT NULL fk on adjustment rows — latest is fine,
        # the ref_id + place_json label them unmistakably as non-trades
        sess = conn.execute("SELECT id FROM vote_sessions ORDER BY id DESC LIMIT 1").fetchone()
        sid = sess["id"] if sess else 0
        members = conn.execute(
            "SELECT m.id, m.display_name FROM members m "
            "JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE COALESCE(m.is_bot,0)=0 AND a.status='connected'").fetchall()
        adjusted = []
        for m in members:
            mid = m["id"]
            live_qty = 0.0
            h = _live_holdings(conn, mid).get(new)
            if h is not None:
                try:
                    live_qty = float(h.get("quantity") or 0)
                except (TypeError, ValueError):
                    live_qty = 0.0
            led_qty = positions.club_shares(mid).get(new, 0.0)
            if led_qty <= _DUST_SH and live_qty <= _DUST_SH:
                continue
            delta = live_qty - led_qty
            if abs(delta) <= _DUST_SH:
                continue
            ref = f"CORPACT:{old}>{new}:{mid}"
            dup = conn.execute("SELECT 1 FROM executions WHERE ref_id=?", (ref,)).fetchone()
            if dup:
                continue   # already migrated once
            conn.execute(
                "INSERT INTO executions(session_id,member_id,symbol,side,type,dollar_amount,"
                "ref_id,status,place_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (sid, mid, new, "buy" if delta > 0 else "sell", "market", 0.0, ref, "placed",
                 json.dumps({"_concord_est_qty": abs(delta),
                             "corporate_action": f"{old}->{new}"}),
                 None, now))
            adjusted.append({"member": m["display_name"], "delta_shares": round(delta, 6)})
        conn.execute("UPDATE universe SET active=0 WHERE symbol=?", (old,))
        conn.execute(
            "INSERT INTO universe(symbol,name,sort_order,active) VALUES(?,?,"
            "COALESCE((SELECT sort_order FROM universe WHERE symbol=?), 9999),1) "
            "ON CONFLICT(symbol) DO UPDATE SET active=1, name=excluded.name",
            (new, name or new, old))
    audit(actor, "symbol_migrated", f"{old}->{new}",
          {"execution_rows": renamed, "adjustments": adjusted})
    return {"old": old, "new": new, "execution_rows": renamed, "adjustments": adjusted}
