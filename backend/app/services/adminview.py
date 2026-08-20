"""Owner+cofounder console view + the morning exception digest — one builder.

`panel()` is shared by GET /api/admin/panel and jobs/admin_digest.py so the
console and the email can never drift apart.

STRICTLY DB-ONLY: nothing in this module may touch Robinhood. Live balance
reads (alloc.member_allocation and friends) cost 1.6s+ per member — the panel
must answer fast and be safe to build from a timer, so every figure here comes
from members/member_settings/rh_accounts/vote_sessions/votes/executions/
audit_log/kv rows that other jobs already maintain.

`will_place_next` re-derives the executor's REAL gate read-only (executor.run:
will_place = settings.is_live AND armed AND not force_dry_run, then the halt
kill switch, the human-quorum floor vs settings.min_human_voters, and the
calendar_covers guard) — with one plain-English reason per failed gate, so
"why won't it trade?" is answerable before 09:31, not after.
"""
from __future__ import annotations

import json

from ..calendar_us import calendar_covers, today_et
from ..config import settings
from ..db import db, is_armed, kv_get
from . import mailer, voting

_PERF_CACHE_KEY = "club_perf_v1"   # performance._CACHE_KEY (kept literal: read-only view)


# ─── state: will the next execution place real orders? ───────────────────────

def _current_open_session() -> dict | None:
    """The open ballot, ignoring STALE rows. A session abandoned mid-cycle stays
    status='open' forever (prod carries one from 2026-06-22), and it would
    otherwise masquerade as "today's ballot" and have its quorum judged instead
    of the session the next run actually executes. The staleness rule itself
    lives in voting.is_abandoned_open — the history views share it."""
    row = voting.latest_session("open")
    if not row or voting.is_abandoned_open(row):
        return None
    return row


def will_place_state() -> dict:
    """The executor's go/no-go, recomputed read-only, with every blocking reason."""
    armed = is_armed()
    halt = kv_get("halt") == "1"
    reasons: list[str] = []
    if not settings.is_live:
        reasons.append("dry_run mode")
    if not armed:
        reasons.append("disarmed")
    if halt:
        reasons.append("halted (kill switch)")
    # Quorum is judged on the session the next run would execute: today's open
    # ballot if there is one, else the sold-awaiting-buy (closed) session.
    sess = _current_open_session() or voting.latest_session("closed")
    if sess is None:
        reasons.append("no vote session")
    else:
        humans = voting.human_voter_count(sess["id"])
        if humans < settings.min_human_voters:
            reasons.append(f"human quorum not met ({humans} < {settings.min_human_voters})")
    if not calendar_covers(today_et()):
        reasons.append("holiday calendar lapsed")
    return {
        "execution_mode": settings.execution_mode,
        "is_live": settings.is_live,
        "armed": armed,
        "halt": halt,
        "will_place_next": not reasons,
        "reasons": reasons,
    }


# ─── members: per-human status + exceptions ──────────────────────────────────

def member_rows() -> list[dict]:
    """Every HUMAN member with the flags an operator triages by. Bots excluded
    (they'd be ~20 noise rows — they get one summary line in cycle_view)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.email, m.display_name, m.role, m.email_verified_at, "
            "s.opted_in, s.alloc_type, s.alloc_value, s.voting_power, "
            "a.account_number, a.agentic_allowed, a.status AS rh_status "
            "FROM members m "
            "LEFT JOIN member_settings s ON s.member_id=m.id "
            "LEFT JOIN rh_accounts a ON a.member_id=m.id "
            # self-service deletion ANONYMIZES rows in place (deleted-N@…) —
            # without this filter every departed member is a forever-exception
            # in the panel and the morning digest
            "WHERE COALESCE(m.is_bot,0)=0 "
            "AND m.email NOT LIKE 'deleted-%@deleted.invalid' ORDER BY m.id"
        ).fetchall()
        # Last-known broker account type per member ('cash' waits T+1 on
        # settlement; 'limited_margin' redeploys same-day). Written by
        # /me/margin-status when a member's wizard probes the broker — a kv
        # read, so the panel stays DB-only. One LIKE scan instead of a kv_get
        # per member; missing/empty (never probed) → null, not "".
        acct_types = {
            r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM kv WHERE key LIKE 'acct_type:%'"
            ).fetchall()
        }
    out: list[dict] = []
    for r in rows:
        connected = bool(r["account_number"]) and r["rh_status"] == "connected"
        agentic = bool(r["agentic_allowed"] or 0)
        opted_in = bool(r["opted_in"] or 0)
        stake_value = float(r["alloc_value"] or 0)
        email_verified = bool(r["email_verified_at"])
        exceptions: list[str] = []
        if not connected and r["rh_status"] != "error":
            exceptions.append("not connected")
        if connected and not agentic:
            exceptions.append("not agentic")
        if not opted_in:
            exceptions.append("opted out")
        if stake_value <= 0:
            exceptions.append("no stake")
        if not email_verified:
            exceptions.append("email unconfirmed")
        if r["rh_status"] == "error":
            exceptions.append("rh status error")
        out.append({
            "id": r["id"],
            "email": r["email"],
            "display_name": r["display_name"],
            "role": r["role"],
            "connected": connected,
            "agentic": agentic,
            "opted_in": opted_in,
            "stake_type": r["alloc_type"] or "percent",
            "stake_value": stake_value,
            "voting_power": float(r["voting_power"] or 0),
            "acct_type": acct_types.get(f"acct_type:{r['id']}") or None,
            "email_verified": email_verified,
            "exceptions": exceptions,
        })
    return out


# ─── cycle: today's ballot + the last run that moved (or planned) orders ─────

def cycle_view() -> dict:
    open_row = _current_open_session()
    open_view = None
    bots_voted = 0
    with db() as conn:
        if open_row:
            sid = open_row["id"]
            humans = voting.human_voter_count(sid)
            picks_so_far = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM votes WHERE session_id=?", (sid,)
            ).fetchone()[0]
            bots_voted = conn.execute(
                "SELECT COUNT(DISTINCT v.member_id) FROM votes v "
                "JOIN members m ON m.id=v.member_id "
                "WHERE v.session_id=? AND COALESCE(m.is_bot,0)=1", (sid,)
            ).fetchone()[0]
            open_view = {
                "id": sid,
                "trade_date": open_row["trade_date"],
                "closes_at": open_row["closes_at"],
                "voters": voting.voter_count(sid),
                "human_voters": humans,
                "quorum_met": humans >= settings.min_human_voters,
                "picks_so_far": picks_so_far,
            }
        last = conn.execute(
            "SELECT s.id, s.trade_date FROM vote_sessions s "
            "WHERE EXISTS (SELECT 1 FROM executions e WHERE e.session_id=s.id) "
            "ORDER BY s.trade_date DESC LIMIT 1"
        ).fetchone()
        last_view = None
        if last:
            by_status = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM executions WHERE session_id=? GROUP BY status",
                (last["id"],)
            ).fetchall()}
            vrow = conn.execute(
                "SELECT detail_json FROM audit_log WHERE action='verify_done' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            verdict = None
            if vrow and vrow["detail_json"]:
                try:
                    verdict = json.loads(vrow["detail_json"]).get("verdict")
                except Exception:  # noqa: BLE001
                    verdict = None
            last_view = {"id": last["id"], "trade_date": last["trade_date"],
                         "by_status": by_status, "latest_verify_verdict": verdict}
        bot_count = conn.execute(
            "SELECT COUNT(*) FROM members WHERE COALESCE(is_bot,0)=1"
        ).fetchone()[0]
    return {"open": open_view, "last_executed": last_view,
            "bots": {"count": bot_count, "voted_today": bots_voted}}


# ─── money + audit ───────────────────────────────────────────────────────────

def money_view(members: list[dict]) -> dict:
    perf = None
    raw = kv_get(_PERF_CACHE_KEY)
    if raw:
        try:
            perf = json.loads(raw)
        except Exception:  # noqa: BLE001
            perf = None
    return {
        "per_member": [{"id": m["id"], "display_name": m["display_name"],
                        "stake_type": m["stake_type"], "stake_value": m["stake_value"],
                        "voting_power": m["voting_power"]} for m in members],
        "club_perf_cached": perf,
    }


def audit_tail(limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT ts, actor, action, target FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── the composed panel ──────────────────────────────────────────────────────

def panel() -> dict:
    """The whole console payload — DB-only, no Robinhood calls, fast."""
    members = member_rows()
    return {
        "state": will_place_state(),
        "members": members,
        "cycle": cycle_view(),
        "money": money_view(members),
        "audit": audit_tail(),
    }


# ─── the morning digest (email body shared with jobs/admin_digest.py) ────────

def digest(p: dict) -> tuple[str, str]:
    """(subject, plain-text body) for a built panel — an ops digest, verdict first.

    "Items needing attention" = every member exception, plus (only in live mode)
    every will-not-place reason — in dry_run the "won't place" verdict is the
    expected state, not an incident.
    """
    state, members, cycle = p["state"], p["members"], p["cycle"]
    n_items = sum(len(m["exceptions"]) for m in members)
    if state["is_live"]:
        n_items += len(state["reasons"])
    subject = (f"Concordia morning check — {n_items} item(s) need attention"
               if n_items else "Concordia morning check — all clear")

    lines = [f"Concordia morning check — {today_et().isoformat()}", ""]
    if state["will_place_next"]:
        lines.append("WILL PLACE: YES — the next execution run will place real orders")
    else:
        lines.append("WILL PLACE: NO — " + "; ".join(state["reasons"]))
    lines.append(f"  mode={state['execution_mode']}"
                 f" armed={'yes' if state['armed'] else 'no'}"
                 f" halt={'yes' if state['halt'] else 'no'}")

    lines += ["", "Member exceptions:"]
    flagged = [m for m in members if m["exceptions"]]
    if flagged:
        for m in flagged:
            lines.append(f"  - {m['display_name']} <{m['email']}>: {', '.join(m['exceptions'])}")
    else:
        lines.append("  none")

    lines += ["", "Cycle preflight:"]
    o = cycle["open"]
    if o:
        lines.append(f"  open ballot {o['trade_date']}: voters={o['voters']}"
                     f" (humans={o['human_voters']},"
                     f" quorum {'met' if o['quorum_met'] else 'NOT met'}),"
                     f" {o['picks_so_far']} symbol(s) on the ballot")
    else:
        lines.append("  no open ballot")
    b = cycle["bots"]
    lines.append(f"  bots: {b['count']} (voted today: {b['voted_today']})")

    lines += ["", "Last executed run:"]
    le = cycle["last_executed"]
    if le:
        by = " ".join(f"{k}={v}" for k, v in sorted(le["by_status"].items()))
        lines.append(f"  {le['trade_date']}: {by or 'no orders'}"
                     f" | verify={le['latest_verify_verdict'] or 'n/a'}")
    else:
        lines.append("  none yet")
    return subject, "\n".join(lines) + "\n"


def digest_recipients() -> list[dict]:
    """Every HUMAN member with an admin-console role — never bots."""
    with db() as conn:
        rows = conn.execute(
            "SELECT email, display_name FROM members "
            "WHERE COALESCE(is_bot,0)=0 AND role IN ('owner','admin') "
            "AND email NOT LIKE 'deleted-%@deleted.invalid' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def send_digest() -> dict:
    """Build + deliver the morning digest. NEVER raises on mail failure.

    Email disabled → the full body is printed to stdout (demo/dev safe) and
    nothing is sent. Per-recipient failures are collected, not raised — the
    systemd timer must not flap on a mail-provider hiccup.
    """
    from html import escape
    p = panel()
    subject, body = digest(p)
    if not settings.email_enabled:
        print(f"[admin_digest] email disabled — digest below\n"
              f"Subject: {subject}\n{body}", flush=True)
        return {"subject": subject, "printed": True, "sent": [], "failed": []}
    html = f"<pre style=\"font-family:monospace\">{escape(body)}</pre>"
    sent: list[str] = []
    failed: list[str] = []
    for r in digest_recipients():
        try:
            ok = mailer.send(r["email"], subject, html, body)
        except Exception as e:  # noqa: BLE001 — a mail crash must not kill the digest
            print(f"[admin_digest] send to {r['email']} raised: {type(e).__name__}: {e}",
                  flush=True)
            ok = False
        (sent if ok else failed).append(r["email"])
    return {"subject": subject, "printed": False, "sent": sent, "failed": failed}
