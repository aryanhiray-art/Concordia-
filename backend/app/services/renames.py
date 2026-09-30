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


def _net_invested(conn, member_id: int, symbol: str) -> float:
    """Net club dollars into a symbol (buys − sells) — the phantom's expected
    value, used to pick the right stray when an account has several."""
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN side='sell' THEN -COALESCE(dollar_amount,0) "
        "ELSE COALESCE(dollar_amount,0) END), 0) v "
        "FROM executions WHERE member_id=? AND symbol=? AND status='placed'",
        (member_id, symbol)).fetchone()
    return float(row["v"] or 0)


def _pair_for_member(conn, mid: int, phantoms: list[str],
                     strays: dict[str, float]) -> list[tuple[str, str]]:
    """Bijective phantom↔stray matches for ONE member.

    The trivial case (one of each) pairs directly. With several strays — the
    common case for an owner who also holds personal positions — a phantom
    pairs with the ONE stray whose live value sits within a generous market-
    drift band (0.5×–2×) of the club's net dollars into the phantom; if zero
    or several strays fit, no pair (ambiguity must fail silent, never guess).
    v2 of the detector: v1 required exactly-one-of-each, which meant the
    AVB→VMRK incident was invisible on the owner's own account and only
    surfaced via a member with a clean account.
    """
    out: list[tuple[str, str]] = []
    if len(phantoms) == 1 and len(strays) == 1:
        return [(phantoms[0], next(iter(strays)))]
    claimed: set[str] = set()
    for x in phantoms:
        expect = _net_invested(conn, mid, x)
        if expect <= _DUST_USD:
            continue
        fits = [s for s, v in strays.items()
                if s not in claimed and 0.5 * expect <= v <= 2.0 * expect]
        if len(fits) == 1:
            out.append((x, fits[0]))
            claimed.add(fits[0])
    return out


def detect() -> list[dict]:
    """[{old, new, members: [names], live_value}] — probable ticker renames."""
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
            strays = {s: float(h.get("value") or 0) for s, h in live.items()
                      if s not in ledger and float(h.get("value") or 0) > _DUST_USD}
            if not phantoms or not strays:
                continue
            for old, new in _pair_for_member(conn, m["id"], phantoms, strays):
                agg = pairs.setdefault((old, new), {"old": old, "new": new,
                                                    "members": [], "live_value": 0.0})
                agg["members"].append(m["display_name"])
                agg["live_value"] += strays[new]
    out = sorted(pairs.values(), key=lambda p: -len(p["members"]))
    for p in out:
        p["live_value"] = round(p["live_value"], 2)
    return out


def check_and_alert() -> list[dict]:
    """Scheduled-path detection (rides the 10-min account refresher): audit +
    email the owner/admins ONCE per newly-seen pair, so a ticker change is
    announced within minutes of the broker converting the shares — not
    whenever someone happens to open the console. kv-deduped; a pair alerts
    again only if it disappears (migrated) and later re-emerges."""
    from ..config import settings
    from ..db import kv_get, kv_set
    from . import adminview, mailer

    found = detect()
    seen_keys = {f"rename_alerted:{p['old']}>{p['new']}" for p in found}
    for p in found:
        key = f"rename_alerted:{p['old']}>{p['new']}"
        if kv_get(key):
            continue
        kv_set(key, str(int(time.time())))
        audit("system", "rename_detected", f"{p['old']}->{p['new']}",
              {"members": p["members"], "live_value": p["live_value"]})
        subject = f"Concordia: ticker change detected — {p['old']} → {p['new']}"
        body = (f"{p['old']} looks renamed to {p['new']} "
                f"(held by {', '.join(p['members'])}, ${p['live_value']:.2f}).\n\n"
                f"The club ledger still tracks {p['old']}, so this position is "
                f"unsellable until migrated. One click on the admin console's "
                f"Overview banner repairs it (or POST /api/admin/rename-symbol).")
        if settings.email_enabled:
            for r in adminview.digest_recipients():
                try:
                    mailer.send(r["email"], subject,
                                f"<pre style=\"font-family:monospace\">{body}</pre>", body)
                except Exception:  # noqa: BLE001 — alerting must never break the refresher
                    pass
        else:
            print(f"[renames] {subject}\n{body}", flush=True)
    # a migrated/vanished pair may alert again if it ever re-appears
    with db() as conn:
        stale = conn.execute(
            "SELECT key FROM kv WHERE key LIKE 'rename_alerted:%'").fetchall()
    for row in stale:
        if row["key"] not in seen_keys:
            kv_set(row["key"], "")
    return found


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
        deferred = []
        for m in members:
            mid = m["id"]
            # The adjustment is sized from the BROKER, read strictly — never
            # from the display snapshot. account_snapshots is a cache the
            # refresher keeps only for opted-in members and leaves stale on a
            # failed refresh; a member whose snapshot is missing or predates
            # the conversion read as live_qty=0 here, which sized a $0 SELL of
            # their entire renamed position — irreversible, and the dup guard
            # below then made migrate a permanent no-op for them.
            try:
                live_map = {str(p["symbol"]).upper(): float(p.get("quantity") or 0.0)
                            for p in positions.live_positions_strict(mid)}
            except Exception as e:  # noqa: BLE001 — no read, no write
                deferred.append({"member": m["display_name"], "reason": f"live read failed: {e}"})
                continue
            live_qty = live_map.get(new, 0.0)
            led_qty = positions.club_shares(mid).get(new, 0.0)
            if led_qty <= _DUST_SH and live_qty <= _DUST_SH:
                continue
            delta = live_qty - led_qty
            if abs(delta) <= _DUST_SH:
                continue
            if delta < 0 and live_qty <= _DUST_SH:
                # The ledger says the member holds the new name and the account
                # shows none of it: that is a member the club cannot see (a
                # positions read that missed it, a conversion not yet booked),
                # not a member who holds nothing. Never write a negative
                # adjustment from a zero read; leave them re-runnable.
                deferred.append({"member": m["display_name"], "reason": "account shows none of "
                                 f"{new} while the ledger holds {round(led_qty, 6)} — not adjusted"})
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
          {"execution_rows": renamed, "adjustments": adjusted, "deferred": deferred})
    return {"old": old, "new": new, "execution_rows": renamed, "adjustments": adjusted,
            "deferred": deferred}
