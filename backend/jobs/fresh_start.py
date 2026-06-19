#!/usr/bin/env python3
"""One-shot FRESH START: liquidate every connected member's CLUB holdings to cash.

Sells only what the club itself bought (club_shares, reconstructed from placed
executions) for every connected agentic member — a member's own non-club positions
are never touched. Records the sells so club_shares nets back to ~0, so the club
restarts from an all-cash base on the next sell day.

Guarded: runs exactly once (kv flag), honors the halt kill switch, market-gated.
systemd: a dated one-shot concord-fresh-start.timer fires it just after the open.
"""
import json
import math
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_trading_day, today_et  # noqa: E402
from app.db import audit, db, init_db, kv_get, kv_set  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.services import positions  # noqa: E402

_DONE = "fresh_start_done"


def _connected_members() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.display_name FROM members m JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE m.is_bot=0 AND a.status='connected' AND a.agentic_allowed=1 ORDER BY m.id"
        ).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    init_db()
    if kv_get(_DONE) == "1":
        print("[fresh_start] already done — skipping")
        return 0
    if kv_get("halt") == "1":
        print("[fresh_start] kill switch (halt) is on — skipping liquidation")
        return 0
    if not is_trading_day(today_et()):
        print("[fresh_start] not a trading day — will retry next run")
        return 0

    total = 0
    for m in _connected_members():
        mid = m["id"]
        acct = vault.account_number_for(mid)
        if not acct:
            continue
        held = positions.club_shares(mid)                       # only what the club bought
        if not held:
            print(f"[fresh_start] {m['display_name']}: no club holdings")
            continue
        live = {p["symbol"]: p["quantity"] for p in positions.live_positions(mid)}
        client = vault.ensure_client(mid)
        sold = []
        for sym, q in held.items():
            qfloor = math.floor(min(q, live.get(sym, 0.0)) * 1e6) / 1e6   # never over-request
            if qfloor <= 0:
                continue
            qstr = f"{qfloor:.6f}"
            ref = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"freshstart:{mid}:{sym}"))
            try:
                place = client.place_market_sell(acct, sym, qstr, ref_id=ref)
                if isinstance(place, dict):
                    place["_concord_est_qty"] = qfloor
                with db() as conn:   # record so club_shares nets back to ~0
                    conn.execute(
                        "INSERT INTO executions(session_id,member_id,symbol,side,type,status,place_json,ref_id,created_at) "
                        "VALUES(0,?,?,?,?,?,?,?,strftime('%s','now'))",
                        (mid, sym, "sell", "market", "placed", json.dumps(place), ref),
                    )
                sold.append({"symbol": sym, "qty": qstr})
                total += 1
                print(f"[fresh_start] {m['display_name']}: sold {qstr} {sym}")
            except Exception as e:  # noqa: BLE001
                print(f"[fresh_start] {m['display_name']}: FAILED to sell {sym}: {e}")
        audit("system", "fresh_start", str(mid), {"sold": sold})

    kv_set(_DONE, "1")
    print(f"[fresh_start] done — {total} sell(s) placed across connected members")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
