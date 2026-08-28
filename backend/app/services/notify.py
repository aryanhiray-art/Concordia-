"""Member trade receipts — the Autopilot-app standard, in email form.

Concordia used to trade member accounts silently; the copy-trading apps it
competes with push a notification for every executed trade. This module is the
email equivalent: after a LIVE run, every HUMAN member whose account the run
touched gets ONE plain-English email — buys/sells placed, deferred buys waiting
on settlement, errored orders (retried automatically next run), and a one-line
taxable-events note when sells went out. A member whose Robinhood connection is
stranded gets the reconnect call-to-action instead. A member with nothing to
report gets nothing.

Constitutionally incapable of hurting a run: executor.run calls this AFTER the
run report is persisted, from a fully fenced call site; the entry point here
swallows everything again, and each member's email is fenced on its own — one
bad address or provider hiccup never blocks the next member's receipt.
email_enabled=False → each receipt prints to stdout instead (demo/dev safe).

Gate: settings.member_run_emails (env MEMBER_RUN_EMAILS, default true).
"""
from __future__ import annotations

from ..config import settings
from ..db import db
from . import membership


def send_run_emails(summary: dict) -> dict:
    """Send (or print) one receipt per touched member for a completed run.

    `summary` is the executor's run summary/report dict. Never raises."""
    out: dict = {"sent": [], "failed": [], "printed": [], "skipped": None}
    try:
        if not settings.member_run_emails:
            out["skipped"] = "member_run_emails disabled"
            return out
        if not summary.get("will_place"):
            out["skipped"] = "not a live run"
            return out
        trade_date = str(summary.get("trade_date") or "")
        sid = _session_id_for(trade_date)
        for m in (summary.get("members") or []):
            try:
                _notify_member(m, trade_date, sid, out)
            except Exception:  # noqa: BLE001 — one member's email never blocks the next
                pass
    except Exception:  # noqa: BLE001 — receipts must never surface a failure
        pass
    return out


# ─── internals ───────────────────────────────────────────────────────────────

def _session_id_for(trade_date: str) -> int | None:
    if not trade_date:
        return None
    try:
        with db() as conn:
            row = conn.execute("SELECT id FROM vote_sessions WHERE trade_date=?",
                               (trade_date,)).fetchone()
        return row["id"] if row else None
    except Exception:  # noqa: BLE001
        return None


def _member_row(member_id) -> dict | None:
    """{email, display_name} for a HUMAN, non-deleted member — else None."""
    if member_id is None:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT email, display_name, COALESCE(is_bot,0) AS is_bot "
            "FROM members WHERE id=?", (member_id,)).fetchone()
    if not row or row["is_bot"] or not row["email"]:
        return None
    if str(row["email"]).startswith("deleted-") and str(row["email"]).endswith("@deleted.invalid"):
        return None
    return {"email": row["email"], "display_name": row["display_name"]}


def _deferred_usd(sid: int | None, member_id) -> float | None:
    """$ of this member's buys deferred for settled cash, from the executions
    ledger (the summary's order rows don't carry the deferred amounts)."""
    if sid is None:
        return None
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(dollar_amount),0) AS usd FROM executions "
                "WHERE session_id=? AND member_id=? AND side='buy' "
                "AND status='skipped' AND error LIKE 'deferred%'",
                (sid, member_id)).fetchone()
        usd = float(row["usd"] or 0)
        return usd if usd > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _notify_member(m: dict, trade_date: str, sid: int | None, out: dict) -> None:
    orders = m.get("orders") or []
    stranded = m.get("skip_reason") == "rh_error_stranded"
    if not orders and not stranded:
        return                                  # untouched member → no email
    row = _member_row(m.get("member_id"))
    if row is None:
        return
    if stranded:
        subject = "Concordia could not trade for you — reconnect Robinhood"
        body = (f"Hi {row['display_name']},\n\n"
                "Concordia could not trade in your account this run: your "
                "Robinhood connection needs to be re-linked.\n\n"
                "Sign in to Concordia and reconnect Robinhood from your Account "
                "page — until then, the club's runs will skip your account.\n")
        _deliver(row["email"], subject, body, out)
        return

    buys = [o for o in orders if o.get("side") == "buy" and o.get("status") == "placed"]
    sells = [o for o in orders if o.get("side") == "sell" and o.get("status") == "placed"]
    errors = [o for o in orders if o.get("status") == "error"]
    deferred = [o for o in orders if o.get("side") == "buy" and o.get("status") == "skipped"
                and "defer" in str(o.get("note") or "")]
    if not (buys or sells or errors or deferred):
        return                                  # nothing happened worth a receipt

    lines: list[str] = [f"Hi {row['display_name']},", "",
                        f"Here's what Concordia did in your account on {trade_date}:", ""]
    for o in buys:
        amt = o.get("amount")
        lines.append(f"  BUY   {o.get('symbol')}"
                     + (f"  ${float(amt):.2f}" if amt is not None else "  placed"))
    for o in sells:
        qty = o.get("qty")
        note = str(o.get("note") or "").strip()
        lines.append(f"  SELL  {o.get('symbol')}"
                     + (f"  {qty} shares" if qty else "")
                     + (f"  ({note})" if note else ""))
    if buys or sells:
        lines.append("")
    if deferred:
        usd = _deferred_usd(sid, m.get("member_id"))
        if usd is not None:
            lines.append(f"${usd:.2f} of buys wait for settled cash — they land "
                         "on the next top-up day.")
        else:
            lines.append(f"{len(deferred)} buy(s) wait for settled cash — they "
                         "land on the next top-up day.")
    if errors:
        lines.append(f"{len(errors)} order(s) hit an error — we retry "
                     "automatically next run.")
    if sells:
        lines.append("")
        lines.append("One note: sells can create taxable events in a brokerage "
                     "account.")
    body = "\n".join(lines) + "\n"
    _deliver(row["email"], f"Concordia traded for you — {trade_date}", body, out)


def _deliver(to: str, subject: str, body: str, out: dict) -> None:
    if not settings.email_enabled:
        print(f"[notify] email disabled — receipt below\n"
              f"To: {to}\nSubject: {subject}\n{body}", flush=True)
        out["printed"].append(to)
        return
    from html import escape

    from . import mailer
    try:
        ok = mailer.send(to, subject,
                         f"<pre style=\"font-family:monospace\">{escape(body)}</pre>",
                         body)
    except Exception:  # noqa: BLE001 — a mail crash is a failed send, nothing more
        ok = False
    (out["sent"] if ok else out["failed"]).append(to)


# ─── ballot lifecycle emails (open / closing-soon) ───────────────────────────
# Same constitution as the receipts above: fenced everywhere, never raises,
# one member's failure never blocks the next. A kv stamp per (kind, session)
# makes every send idempotent — catch-up timer runs can't double-mail the club.
# DEMO_MODE sends nothing: demo ballots are a playground, not an obligation.

def _reminder_recipients(sid: int, only_nonvoters: bool) -> list[dict]:
    """Human, verified, participating members with reminders on — optionally
    only those who have not voted in session `sid`."""
    q = (
        "SELECT m.id, m.email, m.display_name FROM members m "
        "JOIN member_settings s ON s.member_id = m.id "
        "WHERE COALESCE(m.is_bot,0)=0 AND m.email_verified_at IS NOT NULL "
        "AND s.opted_in=1 AND COALESCE(s.email_reminders,1)=1 "
        f"AND {membership.active_clause('m')}"
    )
    if only_nonvoters:
        q += " AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.session_id=? AND v.member_id=m.id)"
    with db() as conn:
        rows = conn.execute(q, ((sid,) if only_nonvoters else ())).fetchall()
    return [dict(r) for r in rows]


def _lifecycle_send(kind: str, session: dict, only_nonvoters: bool,
                    subject: str, body_for) -> dict:
    out: dict = {"sent": [], "failed": [], "printed": [], "skipped": None}
    try:
        if settings.demo_mode:
            out["skipped"] = "demo mode"
            return out
        if not settings.member_run_emails:
            out["skipped"] = "member_run_emails disabled"
            return out
        sid = session.get("id")
        if not sid or session.get("status") != "open":
            out["skipped"] = "no open session"
            return out
        from ..db import kv_get, kv_set
        stamp = f"notified:{kind}:{sid}"
        if kv_get(stamp):
            out["skipped"] = "already sent"
            return out
        kv_set(stamp, str(int(__import__("time").time())))   # stamp FIRST: a re-run mid-send must not re-mail the club
        for r in _reminder_recipients(int(sid), only_nonvoters):
            try:
                _deliver(r["email"], subject, body_for(r), out)
            except Exception:  # noqa: BLE001
                pass
        # Escape hatch: if EVERY delivery failed (provider outage), clear the
        # stamp so the next timer run retries — nothing was delivered, so
        # at-most-once still holds. Partial failure keeps the stamp (the
        # delivered majority must never be double-mailed).
        if out["failed"] and not out["sent"] and not out["printed"]:
            kv_set(stamp, "")
    except Exception:  # noqa: BLE001
        pass
    return out


def send_ballot_open_emails(session: dict) -> dict:
    """One 'the ballot is open' email per participating member, once per session."""
    td = str(session.get("trade_date") or "")

    def body(r: dict) -> str:
        return (f"Hi {r['display_name']},\n\n"
                f"The club's ballot for the {td} cycle is open.\n\n"
                "Cast your picks at voteconcordia.com/vote — your voting power "
                "only counts if you use it before the ballot closes.\n\n"
                "(You can turn these reminders off from your Account page.)\n")
    return _lifecycle_send("open", session, False,
                           f"Concordia — the ballot is open ({td})", body)


def send_closing_soon_emails(session: dict) -> dict:
    """One closing-soon nudge per participating member who has NOT voted yet."""
    td = str(session.get("trade_date") or "")

    def body(r: dict) -> str:
        return (f"Hi {r['display_name']},\n\n"
                f"The ballot for the {td} cycle closes soon and you haven't "
                "voted yet.\n\n"
                "Cast your picks at voteconcordia.com/vote before it closes — "
                "otherwise this cycle trades without your voice.\n\n"
                "(You can turn these reminders off from your Account page.)\n")
    return _lifecycle_send("close", session, True,
                           f"Concordia — the ballot closes soon ({td})", body)
