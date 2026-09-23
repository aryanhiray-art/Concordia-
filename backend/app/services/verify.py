"""Post-trade account verification — the account is the source of truth.

Deterministic (NO LLM, NO writes, NO orders): reads each member's LIVE Robinhood
account and confirms the club's intent was actually carried out, catching exactly
the failures that bit us before — a SELL that errored/rejected so shares weren't
sold, a name that was dropped from the basket but still held, and club-ledger
drift vs the real account.

Used two ways:
  * automatically at the end of executor.run() (fail-safe; never blocks trading)
  * on demand via jobs/verify.py (e.g. after a manual liquidation)

An optional plain-English report is layered on top by services.narrate (gpt-5.5
via OpenClaw) — that runs as root; this module never depends on it.
"""
from __future__ import annotations

import math

import time

from ..db import db
from ..robinhood import vault
from . import membership, positions, voting

# A live position below this dollar value is broker dust (sub-min-sellable), not a
# real un-sold holding — flagging it as "not sold" would be noise.
DUST_USD = 1.00


def _quotes(client, syms: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(syms), 20):
        try:
            res = (client.get_quotes(syms[i:i + 20]).get("data") or {}).get("results") or []
            for e in res:
                q = e.get("quote") or {}
                if q.get("symbol"):
                    _px = positions._num(q.get("last_trade_price"))
                    # Store None unless the price is usable. A NaN would read as
                    # priced=True below while every value test against it reads
                    # False — so a real club holding would be waved through as
                    # "no leftovers" on the expect_flat check, the exact false
                    # PASS this verification exists to catch.
                    out[q["symbol"]] = _px if _px is not None and math.isfinite(_px) else None
        except Exception:  # noqa: BLE001
            pass
    return out


def _recent_error_rows(member_id: int, session_id: int | None) -> list[dict]:
    """Execution rows the club recorded as errored — scoped to the latest session
    when known, so we surface a sell/buy that failed on our side."""
    with db() as conn:
        if session_id is not None:
            rows = conn.execute(
                "SELECT symbol, side, error FROM executions "
                "WHERE member_id=? AND session_id=? AND status='error'",
                (member_id, session_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbol, side, error FROM executions "
                "WHERE member_id=? AND status='error' AND created_at > ?",
                (member_id, int(time.time()) - 86400)).fetchall()
    return [dict(r) for r in rows]


def _latest_session_id(member_id: int) -> int | None:
    with db() as conn:
        r = conn.execute("SELECT MAX(session_id) AS s FROM executions WHERE member_id=?",
                         (member_id,)).fetchone()
    return r["s"] if r and r["s"] is not None else None


def _club_traded_names(member_id: int) -> set[str]:
    """Every symbol the club has ever PLACED an order on — the club/personal
    boundary by symbol. Broader than the net ledger, so a name whose estimate
    ledger nets to ~0 (e.g. a partial-fill sell) is still checked when still held."""
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM executions WHERE member_id=? AND status='placed'",
            (member_id,)).fetchall()
    return {r["symbol"] for r in rows}


def verify_member(member_id: int, *, expect_flat: bool = False) -> dict:
    """Verify ONE member's account against the club's intent.

    expect_flat=True  → the account SHOULD be fully liquidated (e.g. after a manual
                        sell-everything); any club-traded equity left is flagged.
    expect_flat=False → normal rebalance intent: names dropped from the current
                        basket should be gone; names in it may be held.

    Returns {member_id, name, account, verdict: PASS|WARN|FAIL, findings:[...],
             positions:[...], report:str}. Never raises for a single member —
             a read failure is itself a FAIL finding.
    """
    with db() as conn:
        m = conn.execute("SELECT display_name FROM members WHERE id=?", (member_id,)).fetchone()
    name = (m["display_name"] if m else f"member {member_id}")
    acct = vault.account_number_for(member_id)
    findings: list[dict] = []

    def add(level: str, code: str, msg: str) -> None:
        findings.append({"level": level, "code": code, "msg": msg})

    if not acct:
        add("fail", "no_account", "no connected Robinhood account")
        return _finalize(member_id, name, None, findings, [])

    # ── live account truth ────────────────────────────────────────────────────
    try:
        live_list = positions.live_positions_strict(member_id)
    except Exception as e:  # noqa: BLE001
        # couldn't READ the account (throttle/outage) ≠ verified-bad — WARN, not FAIL,
        # so a transient RH hiccup on the post-trade path doesn't cry wolf.
        add("warn", "read_failed", f"could not read live positions to verify: {type(e).__name__}: {e}")
        return _finalize(member_id, name, acct, findings, [])
    live = {p["symbol"]: p["quantity"] for p in live_list}
    client = vault.ensure_client(member_id)
    ledger = positions.club_shares(member_id)
    px = _quotes(client, sorted(set(live) | set(ledger)))
    # `priced=False` when the quote is missing — NEVER let a missing quote collapse
    # a real holding to $0 and wave it through as dust (that was a false-PASS).
    pos_rows = sorted(
        ({"symbol": s, "quantity": round(q, 6),
          "value": round(q * (px.get(s) or 0.0), 2), "priced": px.get(s) is not None}
         for s, q in live.items()),
        key=lambda r: r["value"], reverse=True)

    # ── 1. errored orders the club recorded (a sell/buy that failed on our side) ─
    sid = _latest_session_id(member_id)
    for r in _recent_error_rows(member_id, sid):
        add("fail", "errored_order",
            f"{r['side'].upper()} {r['symbol']} errored — {(r['error'] or '')[:90]}")

    # Club/personal boundary by SYMBOL = every name the club has EVER traded, NOT the
    # net-positive ledger (which can net a partial-fill name to ~0 and then skip a
    # real still-held position). Intersected with live, so fully-sold names aren't hit.
    club_names = _club_traded_names(member_id)
    club_held = [p for p in pos_rows if p["symbol"] in club_names]
    unpriced = [p for p in club_held if not p["priced"] and p["quantity"] > 1e-6]

    # ── 2. "everything should be sold" (manual liquidation check) ───────────────
    if expect_flat:
        if unpriced:
            add("fail", "unpriced_holding",
                "cannot confirm sold — still holds (no quote): "
                + ", ".join(f"{p['symbol']} {p['quantity']}sh" for p in unpriced))
        leftover = [p for p in club_held if p["priced"] and p["value"] > DUST_USD]
        if leftover:
            add("fail", "not_flat",
                "account NOT fully sold — still holds "
                + ", ".join(f"{p['symbol']} {p['quantity']} (${p['value']})" for p in leftover))
        if not unpriced and not leftover:
            dust = [p for p in club_held if p["value"] > 0]
            add("ok", "flat",
                "account is flat on club names"
                + (f" (only sub-$1 dust: {', '.join(p['symbol'] for p in dust)})" if dust else ""))
    else:
        # ── 2b. names DROPPED from the current basket must be gone ──────────────
        # Pick the basket by RECENCY across statuses: on a sell day today's session is
        # 'closed' and the prior is 'executed'; preferring 'executed' would use the
        # STALE prior basket that still lists the just-dropped name (a false PASS).
        cands = [s for s in (voting.latest_session("executed"),
                             voting.latest_session("closed")) if s]
        sess = max(cands, key=lambda s: s.get("trade_date") or "", default=None)
        basket = set()
        if sess:
            try:
                basket = {p["symbol"] for p in voting.session_picks(sess)}
            except Exception:  # noqa: BLE001
                basket = set()
        # a dropped club name still held (or unpriced) means the exit didn't complete
        stuck = [p for p in club_held if p["symbol"] not in basket
                 and (not p["priced"] or p["value"] > DUST_USD)]
        if stuck:
            add("fail", "exit_incomplete",
                "dropped from basket but still held (sell did not complete): "
                + ", ".join(f"{p['symbol']} {'(no quote)' if not p['priced'] else '$' + str(p['value'])}"
                            for p in stuck))

    # ── 3. club-ledger drift vs the real account (root cause of the dust mess) ──
    phantom = [s for s, q in ledger.items() if q > 1e-6 and live.get(s, 0.0) <= 1e-9]
    if phantom:
        add("warn", "ledger_phantom",
            f"ledger lists {len(phantom)} name(s) the account no longer holds "
            f"({', '.join(sorted(phantom)[:8])}{'…' if len(phantom) > 8 else ''}) — "
            "reconcile drift, not a trade problem")

    if not any(f["level"] in ("warn", "fail") for f in findings):
        add("ok", "clean", "account matches club intent; no errored orders")
    return _finalize(member_id, name, acct, findings, pos_rows)


def _finalize(member_id, name, acct, findings, pos_rows) -> dict:
    verdict = ("FAIL" if any(f["level"] == "fail" for f in findings)
               else "WARN" if any(f["level"] == "warn" for f in findings) else "PASS")
    icon = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    acct_mask = f"••••{acct[-4:]}" if acct else "—"
    lines = [f"{name} ({acct_mask}) — {verdict}"]
    for f in findings:
        lines.append(f"  {icon.get(f['level'], '·')} {f['msg']}")
    if pos_rows:
        held = ", ".join(f"{p['symbol']} ${p['value']}" for p in pos_rows[:12]) or "flat"
        lines.append(f"  · holds {len(pos_rows)}: {held}")
    return {"member_id": member_id, "name": name, "account": acct, "verdict": verdict,
            "findings": findings, "positions": pos_rows, "report": "\n".join(lines)}


def verify_all(*, expect_flat: bool = False) -> dict:
    """Verify every opted-in, connected member. Returns
    {verdict, members:[...], report:str} — worst member verdict wins."""
    with db() as conn:
        mids = [r["id"] for r in conn.execute(
            "SELECT m.id FROM members m JOIN member_settings s ON s.member_id=m.id "
            "JOIN rh_accounts a ON a.member_id=m.id "
            f"WHERE s.opted_in=1 AND a.status='connected' AND a.agentic_allowed=1 "
            f"AND {membership.active_clause('m')} ORDER BY m.id").fetchall()]
    members = []
    for mid in mids:
        try:
            members.append(verify_member(mid, expect_flat=expect_flat))
        except Exception as e:  # noqa: BLE001 — one member never sinks the whole check
            members.append({"member_id": mid, "name": f"member {mid}", "verdict": "FAIL",
                            "findings": [{"level": "fail", "code": "verify_crash",
                                          "msg": f"{type(e).__name__}: {e}"}],
                            "positions": [], "report": f"member {mid} — FAIL (verify crashed: {e})"})
    order = {"FAIL": 2, "WARN": 1, "PASS": 0}
    verdict = "PASS"
    for m in members:
        if order[m["verdict"]] > order[verdict]:
            verdict = m["verdict"]
    report = f"ACCOUNT VERIFICATION — {verdict}\n" + "\n\n".join(m["report"] for m in members)
    return {"verdict": verdict, "members": members, "report": report}
