#!/usr/bin/env python3
"""One-shot FRESH START: liquidate every connected member's account to all-cash.

Sells EVERY long position for every connected agentic member (both accounts are
test accounts), so the club restarts from a clean cash base on the next sell day.
Records the sells so club_shares nets back to ~0.

Guarded: runs exactly once (kv flag), honors the halt kill switch, market-gated.
systemd: a dated one-shot concord-fresh-start.timer fires it just after the open.
"""
import json
import math
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_trading_day, today_et  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import audit, db, init_db, is_armed, kv_get, kv_set  # noqa: E402
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
    # Hardening (finding #6): fresh_start places REAL sells outside the executor's
    # triple gate, so it now demands the same live+ARMED authority PLUS an explicit
    # one-shot env opt-in, and defaults to CLUB-ONLY scope. A stray flag-clear or a
    # partial DB restore can no longer market-sell a member's ENTIRE personal book.
    if os.environ.get("FRESH_START_ALLOW", "").strip() != "1":
        print("[fresh_start] FRESH_START_ALLOW is not '1' — refusing to liquidate. "
              "Set it in the job env to authorize a fresh start.")
        return 0
    if not (settings.is_live and is_armed()):
        print("[fresh_start] not live+ARMED — refusing real-money liquidation")
        return 0
    # 'club' (default) sells ONLY club-bought shares (never personal holdings);
    # 'all' liquidates every long position (the original test-account behaviour).
    scope = os.environ.get("FRESH_START_SCOPE", "club").strip().lower()

    total = 0
    for m in _connected_members():
        mid = m["id"]
        acct = vault.account_number_for(mid)
        if not acct:
            continue
        # Live account holdings — we sell the FULL live quantity of each name so
        # nothing fractional is left behind (a 6dp floor of a drifted ledger
        # estimate strands crumbs). scope='all' liquidates every long position;
        # 'club' liquidates only names the club bought (never personal symbols),
        # but sells the entire live quantity OF those names so the position closes.
        live = {p["symbol"]: p["quantity"] for p in positions.live_positions(mid)}
        if scope == "all":
            names = list(live)
        else:
            names = [s for s in positions.club_shares(mid) if live.get(s, 0.0) > 0]
        if not names:
            print(f"[fresh_start] {m['display_name']}: account already all-cash")
            continue
        client = vault.ensure_client(mid)
        sold = []
        for sym in names:
            q = live.get(sym, 0.0)                # the ENTIRE live position → no residue
            qfloor = math.floor(q * 1e8) / 1e8   # broker precision (8dp), never above held
            if qfloor <= 0:
                continue
            qstr = f"{qfloor:.8f}"
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
