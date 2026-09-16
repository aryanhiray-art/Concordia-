#!/usr/bin/env python3
"""One-shot: liquidate the owner's account to all-cash (the "start at $100" reset).

Sells EVERY long position in the owner's agentic account at market, so they begin
the bot test from a clean cash base. Guarded by a kv flag so it runs exactly once
("after tmrw ignore"); the systemd one-shot timer fires it just after the open.
Market-hours gated — does nothing on a non-trading day.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar_us import is_trading_day, today_et  # noqa: E402
from app.db import audit, db, init_db, kv_get, kv_set  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.services import positions  # noqa: E402
from app.services.executor import MIN_SELL_QTY, floor_qty6  # noqa: E402

_DONE = "reset_account_done"


def _owner_id() -> int | None:
    with db() as conn:
        row = conn.execute("SELECT id FROM members WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def main() -> int:
    init_db()
    if kv_get(_DONE) == "1":
        print("[reset_account] already done — skipping")
        return 0
    # Honor the kill switch: hitting Kill on the dashboard (halt=1) cancels the
    # scheduled real-money liquidation, same as it halts the executor.
    if kv_get("halt") == "1":
        print("[reset_account] kill switch (halt) is on — skipping liquidation")
        return 0
    if not is_trading_day(today_et()):
        print("[reset_account] not a trading day — will retry next run")
        return 0
    mid = _owner_id()
    if not mid:
        print("[reset_account] no owner found")
        return 0
    acct = vault.account_number_for(mid)
    pos = positions.live_positions(mid)
    if not acct:
        print("[reset_account] owner not connected")
        return 0
    if not pos:
        print("[reset_account] account already all-cash — nothing to sell")
        kv_set(_DONE, "1")
        return 0
    client = vault.ensure_client(mid)
    sold = []
    for p in pos:
        sym, qty = p["symbol"], p["quantity"]
        qf = floor_qty6(qty)                 # float-safe 6dp floor, never above held
        if qf <= 0:
            continue
        if qf < MIN_SELL_QTY:
            print(f"[reset_account] {sym}: residue below broker sell minimum — left in place")
            continue
        qstr = f"{qf:.6f}"
        ref = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"reset:{mid}:{sym}"))
        try:
            client.place_market_sell(acct, sym, qstr, ref_id=ref)
            sold.append({"symbol": sym, "qty": qstr})
            print(f"[reset_account] sold {qstr} {sym}")
        except Exception as e:  # noqa: BLE001
            print(f"[reset_account] FAILED to sell {sym}: {e}")
    audit("system", "reset_account", str(mid), {"sold": sold})
    kv_set(_DONE, "1")
    print(f"[reset_account] done — liquidated {len(sold)} position(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
