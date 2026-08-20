"""Per-member DISPLAY snapshot of the Robinhood-backed account view.

GET /api/me/account and GET /api/me/holdings used to do LIVE broker reads on
EVERY page visit (~1.6s and ~2.3s), so opening Overview cost ~4s and five HTTP
round-trips to Robinhood — repeated every time the member clicked between
Overview and Account. This module reads the broker ONCE per member per interval,
stores the result in `account_snapshots`, and serves those routes from that row:
an instant local DB read.

╔══════════════════════════════════════════════════════════════════════════════╗
║ SAFETY BOUNDARY — this is a DISPLAY layer. The TRADING path must NEVER read  ║
║ it. alloc.member_balance(), alloc.rebalance_budget() and                     ║
║ positions.holdings_with_value() stay live + uncached, and services/executor  ║
║ (its own _portfolio() fetch), services/power and services/reputation keep    ║
║ calling them directly. An order sized off a stale balance could over-order   ║
║ real money. Only the HTTP read routes in app/routes/members.py may use this. ║
╚══════════════════════════════════════════════════════════════════════════════╝

Freshness model — stale-while-revalidate, so a member never waits on Robinhood:
    no row yet        one BLOCKING refresh (first load after signup/connect only)
    age <= FRESH_SECS serve the row as-is, zero broker calls
    age  > FRESH_SECS serve the row IMMEDIATELY, refresh in a background thread
    age  > STALE_SECS same, but flagged stale=true so the UI can say how old it is
The interval refresher (jobs/refresh_accounts.py, every 10 min) normally wins the
race, so in practice members pay for a live read only on their very first load.

A refresh costs one get_portfolio + one get_positions + one batched get_quotes
(club_pnl reuses the holdings list rather than re-reading it).
"""
from __future__ import annotations

import json
import threading
import time

from ..db import db
from . import alloc, equity_history, positions, power

# How long a snapshot is served without even trying to refresh it. Matches the
# 10-minute interval refresher, so a warm member is normally served by the timer.
FRESH_SECS = 600
# Past this the data is flagged `stale` for the UI ("last updated 40 min ago").
# It is still served — old numbers beat a spinner or an empty page.
STALE_SECS = 1800
# Minimum gap between BACKGROUND refresh attempts for one member. Irrelevant on
# the happy path (a success resets the age), but it stops a member hammering the
# broker once every page load while Robinhood is down / their token is dead.
RETRY_GAP_SECS = 60

_lock = threading.Lock()
_inflight: set[int] = set()          # members with a refresh running right now
_last_attempt: dict[int, float] = {}  # member_id -> monotonic-ish ts of last attempt


# ─── build + persist ─────────────────────────────────────────────────────────

def _empty_payload() -> dict:
    """The exact shapes alloc/power return for a member with no usable account —
    what the old endpoints already answered for someone not connected to
    Robinhood. Used only when the very first refresh fails and there is no good
    data to keep, so the routes still return a valid body instead of a 500."""
    return {
        "allocation": {"type": "percent", "value": 0.0, "resolved_usd": 0.0, "balance": None},
        "pnl": {"invested": 0.0, "value": 0.0, "profit": 0.0, "profit_pct": None, "positions": 0},
        "power": {"power": 0.0, "delta": None, "as_of_week": None, "history": []},
    }


def build(member_id: int) -> dict:
    """Do the live reads ONCE and return {allocation, pnl, power, holdings}.

    holdings_with_value is called a single time and handed to club_pnl (which
    would otherwise read the positions + quotes all over again), so a refresh
    costs one holdings read, not two.

    Never raises for a member with no connected Robinhood account: alloc and
    positions already degrade to balance=None / [] / zeroed P&L for them, which
    is exactly what the endpoints returned before this cache existed.
    """
    holds = positions.holdings_with_value(member_id)
    return {
        "allocation": alloc.member_allocation(member_id),
        "pnl": alloc.club_pnl(member_id, holds=holds),
        "power": power.member_power_view(member_id),
        "holdings": holds,
    }


def _store(member_id: int, payload: dict, holdings: list, refreshed_at: int,
           ok: bool, error: str | None) -> dict:
    with db() as conn:
        conn.execute(
            "INSERT INTO account_snapshots(member_id,payload,holdings,refreshed_at,ok,error) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(member_id) DO UPDATE SET "
            "payload=excluded.payload, holdings=excluded.holdings, "
            "refreshed_at=excluded.refreshed_at, ok=excluded.ok, error=excluded.error",
            (member_id, json.dumps(payload), json.dumps(holdings),
             refreshed_at, 1 if ok else 0, error),
        )
    return {"member_id": member_id, "payload": payload, "holdings": holdings,
            "refreshed_at": refreshed_at, "ok": ok, "error": error}


def read(member_id: int) -> dict | None:
    """The stored row, JSON-parsed — or None if there isn't one (or it's corrupt)."""
    with db() as conn:
        row = conn.execute(
            "SELECT payload, holdings, refreshed_at, ok, error FROM account_snapshots "
            "WHERE member_id=?", (member_id,)
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
        holdings = json.loads(row["holdings"])
    except (TypeError, ValueError):
        return None          # unreadable row → treat as absent, next get() rebuilds it
    if not isinstance(payload, dict) or not isinstance(holdings, list):
        return None
    return {"member_id": member_id, "payload": payload, "holdings": holdings,
            "refreshed_at": int(row["refreshed_at"]), "ok": bool(row["ok"]),
            "error": row["error"]}


def refresh(member_id: int) -> dict:
    """Live-read the member's account and persist the snapshot. Returns the row.

    On a broker failure the PREVIOUS payload is KEPT (a transient Robinhood error
    must never blank out good numbers on the member's dashboard) and only ok/error
    flip — refreshed_at deliberately stays at the last SUCCESSFUL read so `as_of`
    never lies about how old the numbers are.

    This is also where the member's account-value HISTORY is recorded
    (services/equity_history): the balance was just read for the cache, so the
    point is free, and only the success path below reaches the write — the
    failure branch returns first, so a broker outage leaves a gap in the chart
    rather than a fake $0.
    """
    with _lock:
        _last_attempt[member_id] = time.time()
    try:
        snap = build(member_id)
    except Exception as e:  # noqa: BLE001 — a display refresh must never propagate
        err = f"{type(e).__name__}: {e}"[:300]
        prev = read(member_id)
        if prev:
            return _store(member_id, prev["payload"], prev["holdings"],
                          prev["refreshed_at"], False, err)
        # Nothing good to keep: store empty shapes so the routes stay 200 and the
        # next request can retry rather than blocking on the broker again.
        return _store(member_id, _empty_payload(), [], int(time.time()), False, err)
    holdings = snap.pop("holdings")
    at = int(time.time())
    row = _store(member_id, snap, holdings, at, True, None)
    # Success only, and a no-op when balance is None (member not connected /
    # partial read). Never raises — see equity_history.record_from_payload.
    equity_history.record_from_payload(member_id, snap, at)
    return row


def invalidate(member_id: int) -> None:
    """Drop the snapshot — the next read does a blocking rebuild. Call whenever the
    underlying account changes identity (Robinhood connect / disconnect), so a
    fresh connection can't be served a pre-connection snapshot."""
    with db() as conn:
        conn.execute("DELETE FROM account_snapshots WHERE member_id=?", (member_id,))
    with _lock:
        _last_attempt.pop(member_id, None)   # let the rebuild happen immediately


# ─── route-facing read ───────────────────────────────────────────────────────

def _kick(member_id: int) -> threading.Thread | None:
    """Refresh in a daemon thread. The in-flight set + retry gap mean concurrent
    requests can't stampede the broker: at most one refresh per member at a time,
    and at most one attempt per RETRY_GAP_SECS while refreshes keep failing.
    Returns the thread (tests join it); None if a refresh was already covered."""
    now = time.time()
    with _lock:
        if member_id in _inflight or now - _last_attempt.get(member_id, 0.0) < RETRY_GAP_SECS:
            return None
        _inflight.add(member_id)
        _last_attempt[member_id] = now

    def _run() -> None:
        try:
            refresh(member_id)
        except Exception:  # noqa: BLE001 — refresh already swallows; belt and braces
            pass
        finally:
            with _lock:
                _inflight.discard(member_id)

    t = threading.Thread(target=_run, name=f"acctsnap-{member_id}", daemon=True)
    t.start()
    return t


def _view(row: dict, *, live: bool) -> dict:
    """Row → the dict the read routes slice their responses out of."""
    as_of = int(row["refreshed_at"])
    return {
        "allocation": row["payload"].get("allocation"),
        "pnl": row["payload"].get("pnl"),
        "power": row["payload"].get("power"),
        "holdings": row["holdings"],
        "as_of": as_of,
        "stale": (int(time.time()) - as_of) > STALE_SECS,
        "live": live,
    }


def get(member_id: int) -> dict:
    """Stale-while-revalidate entry point for the read routes.

    Returns {allocation, pnl, power, holdings, as_of, stale, live}; `live` is True
    only when this very call did the broker read (first load / forced refresh).
    """
    row = read(member_id)
    if row is None:
        return _view(refresh(member_id), live=True)     # first load only
    if row["ok"] and (int(time.time()) - row["refreshed_at"]) <= FRESH_SECS:
        return _view(row, live=False)
    _kick(member_id)                                    # revalidate behind the response
    return _view(row, live=False)


def refresh_view(member_id: int) -> dict:
    """Force a live refresh and return the same view `get` does (live=True).
    For the explicit refresh button and for PUT /me/allocation, where the member
    must see their new stake resolved immediately."""
    return _view(refresh(member_id), live=True)


# ─── interval refresher ──────────────────────────────────────────────────────

def connected_member_ids(opted_in_only: bool = True) -> list[int]:
    """Non-bot members whose Robinhood account is connected (and, by default, who
    are opted in) — the population the interval refresher warms."""
    sql = ("SELECT m.id AS id FROM members m "
           "JOIN rh_accounts a ON a.member_id=m.id "
           "JOIN member_settings s ON s.member_id=m.id "
           "WHERE COALESCE(m.is_bot,0)=0 AND a.status='connected' "
           "AND a.account_number IS NOT NULL")
    if opted_in_only:
        sql += " AND s.opted_in=1"
    with db() as conn:
        return [int(r["id"]) for r in conn.execute(sql + " ORDER BY m.id").fetchall()]


def ensure_acct_type(mid: int) -> None:
    """Backfill the kv `acct_type:{mid}` (cash/margin/limited_margin) when it is
    MISSING — one get_accounts call, then cached forever. The member-side margin
    probe keeps it fresh on changes; this exists so the admin console's ACCT
    column never depends on a member happening to open their Account page
    (Charlie's badge was blank for exactly that reason)."""
    from ..db import kv_get, kv_set
    from ..robinhood import vault
    if kv_get(f"acct_type:{mid}"):
        return
    acct = vault.account_number_for(mid)
    if not acct:
        return
    try:
        client = vault.ensure_client(mid)
        accounts = (client.get_accounts().get("data") or {}).get("accounts") or []
        row = next((a for a in accounts if a.get("account_number") == acct), None)
        if row and row.get("type"):
            kv_set(f"acct_type:{mid}", row["type"])
    except Exception:  # noqa: BLE001 — display metadata; next run retries
        pass


def refresh_all_connected(sleep_s: float = 1.0, opted_in_only: bool = True) -> dict:
    """Refresh every eligible member SEQUENTIALLY with a small gap between them —
    never a burst of parallel reads at the broker. Returns a summary for the job's
    log line. Individual failures are recorded on the row, never raised."""
    ids = connected_member_ids(opted_in_only)
    started, failed = time.time(), []
    for i, mid in enumerate(ids):
        if i and sleep_s:
            time.sleep(sleep_s)
        row = refresh(mid)
        if not row["ok"]:
            failed.append(mid)
        ensure_acct_type(mid)
    return {"members": len(ids), "ok": len(ids) - len(failed), "failed": failed,
            "seconds": round(time.time() - started, 1)}
