"""The unattended-week health sweep — every failure mode a silent week can hit.

The club now runs a full week untouched (ballot opens Thu/Fri eve → weekend
voting → Mon close + both-run → Tue top-up), so anything that quietly breaks
mid-week — a member's dead Robinhood token, an open-vote that never fired, a
disarmed executor on a cycle morning, a stuck session, a filling disk — used to
wait for a human to notice. This sweep (jobs/watchdog.py, every 30 min
06:00–18:30 ET) checks each of those conditions against the DB and alerts ONCE
per condition.

Alert mechanics = the renames.check_and_alert pattern exactly:
  - kv key 'wd:{check-key}' marks an announced condition (value = JSON
    {severity, message, since} so the admin panel can render active alerts
    without re-running any check).
  - a condition that CLEARS gets its kv emptied, so a recurrence re-alerts.
  - every NEW alert → audit("system","watchdog_alert",...) + ONE batched email
    per sweep to the admin roster; W1/W12 additionally mail the affected
    member. email_enabled=False → everything prints to stdout instead.

Constitutionally harmless: every check runs under its own try/except (a broken
check logs and moves on), delivery is fenced per email, and the sweep is
DB-only except the single LLM-probe network call in bots-idle. Nothing here can
touch an order.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path

from ..calendar_us import at_et, cycle_phase, next_trading_day, now_et
from ..config import settings
from ..db import audit, db, is_armed, kv_get, kv_set
from . import voting
from . import membership

_KV_PREFIX = "wd:"

# W9: an equity step bigger than this fraction, with no executions around it,
# is a suspected deposit/withdrawal.
FLOW_STEP_PCT = 0.02
# W9: executions within this many seconds of either equity point explain the
# step as trading, not a flow.
FLOW_EXEC_SLACK_S = 3600
# W11: a newest backup older than this is stale.
BACKUP_STALE_H = 48
# W10: disk holding the DB fuller than this is critical.
DISK_CRIT_PCT = 90.0


# ─── W1: member token dead ───────────────────────────────────────────────────

def _check_member_token_dead() -> list[dict]:
    """Any opted-in human stranded at rh_accounts.status='error' — they will
    silently not trade next run. Also mails the member (their account, their
    action)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.display_name, m.email FROM members m "
            "JOIN member_settings s ON s.member_id=m.id AND s.opted_in=1 "
            "JOIN rh_accounts a ON a.member_id=m.id "
            "WHERE COALESCE(m.is_bot,0)=0 AND a.status='error' "
            f"AND {membership.active_clause('m')}"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        body = (f"Hi {r['display_name']},\n\n"
                "Concordia lost its connection to your Robinhood account, so the "
                "club cannot trade for you until it is re-linked.\n\n"
                "Sign in to Concordia and reconnect Robinhood from your Account "
                "page — it takes about a minute.\n")
        out.append({
            "key": f"member-token-dead:{r['id']}",
            "severity": "crit",
            "message": (f"{r['display_name']}'s Robinhood needs reconnect — "
                        "they will not trade next run"),
            "notify": {"email": r["email"],
                       "subject": "Concordia: your Robinhood needs a reconnect",
                       "body": body},
        })
    return out


# ─── W2: no session on the eve of a vote day ─────────────────────────────────

def _check_no_session_on_eve() -> list[dict]:
    """After 18:30 ET: if the next trading day is a VOTE day (sell/both) and no
    open ballot exists, open-vote failed. (A 'buy' top-up day executes the
    already-closed session and needs no ballot — requiring one would false-alarm
    every Monday evening.)"""
    now = now_et()
    if (now.hour, now.minute) < (18, 30):
        return []
    nxt = next_trading_day(now.date())
    if cycle_phase(nxt) not in ("sell", "both"):
        return []
    row = voting.latest_session("open")
    if row and not voting.is_abandoned_open(row):
        return []
    return [{"key": "no-session-on-eve", "severity": "crit",
             "message": (f"no open ballot on the eve of {nxt.isoformat()} "
                         "(a vote day) — open-vote failed")}]


def _open_session_closing_within(hours: float) -> dict | None:
    row = voting.latest_session("open")
    if not row or voting.is_abandoned_open(row):
        return None
    closes_at = row.get("closes_at") or 0
    if not (0 < closes_at - now_et().timestamp() <= hours * 3600):
        return None
    return row


# ─── W3: quorum risk ─────────────────────────────────────────────────────────

def _check_quorum_risk() -> list[dict]:
    """Ballot closes within 18h and zero humans have voted — the run will be
    review-only (min_human_voters floor) unless someone votes."""
    row = _open_session_closing_within(18)
    if not row or voting.human_voter_count(row["id"]) > 0:
        return []
    return [{"key": f"quorum-risk:{row['id']}", "severity": "warn",
             "message": (f"ballot {row['trade_date']} closes within 18h with 0 "
                         "human voters — without a human vote the run will be "
                         "review-only")}]


# ─── W4: bots idle ───────────────────────────────────────────────────────────

def _check_bots_idle() -> list[dict]:
    """Ballot closes within 18h, no bot has voted, AND the LLM endpoint is
    unreachable (the sweep's one network call — only made when the bots are
    already suspiciously silent)."""
    row = _open_session_closing_within(18)
    if not row:
        return []
    with db() as conn:
        n_bots = conn.execute(
            "SELECT COUNT(*) FROM members WHERE COALESCE(is_bot,0)=1").fetchone()[0]
        voted = conn.execute(
            "SELECT COUNT(DISTINCT v.member_id) FROM votes v "
            "JOIN members m ON m.id=v.member_id "
            "WHERE v.session_id=? AND COALESCE(m.is_bot,0)=1", (row["id"],)
        ).fetchone()[0]
    if n_bots == 0 or voted > 0:
        return []
    from ..bots import ollama
    probe = ollama.probe()
    if probe.get("reachable"):
        return []          # endpoint fine — the bot-vote timer may just not have fired yet
    return [{"key": f"bots-idle:{row['id']}", "severity": "warn",
             "message": (f"bots 0/{n_bots} voted on ballot {row['trade_date']} and the "
                         f"LLM endpoint is unreachable ({probe.get('detail')})")}]


# ─── W5: halted/disarmed on a cycle morning ──────────────────────────────────

def _check_halted_or_disarmed() -> list[dict]:
    """Cycle-day morning (07:00 ET → execute time), live mode, but halted or
    disarmed — today's run will place nothing."""
    now = now_et()
    if cycle_phase(now.date()) is None or not settings.is_live:
        return []
    start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    try:
        end = at_et(now.date(), settings.execute_at)
    except Exception:  # noqa: BLE001
        end = now.replace(hour=10, minute=15, second=0, microsecond=0)
    if not (start <= now <= end):
        return []
    halt = kv_get("halt") == "1"
    if not halt and is_armed():
        return []
    why = "halted (kill switch)" if halt else "disarmed"
    return [{"key": "halted-or-disarmed-on-cycle-morning", "severity": "warn",
             "message": (f"cycle morning and the executor is {why} — "
                         "today's run will place nothing")}]


# ─── W6: missed run ──────────────────────────────────────────────────────────

def _check_missed_run() -> list[dict]:
    """Today had a phase, execute time + 45 min has passed, and NO run_reports
    row exists for today — the run never happened at all."""
    now = now_et()
    today = now.date()
    phase = cycle_phase(today)
    if phase is None:
        return []
    try:
        cut = at_et(today, settings.execute_at)
    except Exception:  # noqa: BLE001
        cut = now.replace(hour=10, minute=15, second=0, microsecond=0)
    if now < cut + _dt.timedelta(minutes=45):
        return []
    midnight = int(at_et(today, "00:00").timestamp())
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM run_reports WHERE trade_date=? OR started_at>=? LIMIT 1",
            (today.isoformat(), midnight)).fetchone()
    if row:
        return []
    return [{"key": f"missed-run:{today.isoformat()}", "severity": "crit",
             "message": (f"no execute run recorded today ({today.isoformat()}, "
                         f"phase '{phase}') — the {settings.execute_at} ET run "
                         "appears to have been missed entirely")}]


# ─── W7: verify failed ───────────────────────────────────────────────────────

def _check_verify_failed() -> list[dict]:
    """The latest run report (today/yesterday) ended WARN or FAIL — the
    post-run account verification found drift the owner should look at."""
    with db() as conn:
        row = conn.execute(
            "SELECT id, trade_date, started_at, verify_verdict FROM run_reports "
            "ORDER BY id DESC LIMIT 1").fetchone()
    if not row or row["verify_verdict"] not in ("WARN", "FAIL"):
        return []
    if (row["started_at"] or 0) < now_et().timestamp() - 2 * 86400:
        return []          # an old failing report shouldn't alarm forever
    return [{"key": "verify-failed", "severity": "warn",
             "message": (f"latest run ({row['trade_date'] or 'unknown date'}) "
                         f"verified {row['verify_verdict']} — check the run "
                         "report's verification findings")}]


# ─── W8: stuck session ───────────────────────────────────────────────────────

def _check_stuck_session() -> list[dict]:
    """A session still 'closed' more than 2 trading days after its trade date is
    stuck in fill-recovery limbo (the exact session-14 incident shape)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, trade_date FROM vote_sessions WHERE status='closed'").fetchall()
    today = now_et().date()
    out: list[dict] = []
    for r in rows:
        try:
            y, m, d = (int(x) for x in str(r["trade_date"]).split("-"))
            sd = _dt.date(y, m, d)
        except Exception:  # noqa: BLE001
            continue
        n, cur = 0, sd
        while cur < today and n <= 3:      # bounded: >2 is all we need to know
            cur = next_trading_day(cur)
            n += 1
        if n > 2:
            out.append({"key": f"stuck-session:{r['id']}", "severity": "warn",
                        "message": (f"session {r['id']} ({r['trade_date']}) is still "
                                    "'closed' more than 2 trading days on — "
                                    "fill-recovery limbo; investigate")})
    return out


# ─── W9: suspected deposit/withdrawal ────────────────────────────────────────

def _check_suspected_flow() -> list[dict]:
    """A member's latest two equity points differ >2% with NO executions in the
    surrounding window — likely a deposit/withdrawal. Recorded once per step in
    member_flows (foundation for deposit-aware returns; detection only)."""
    out: list[dict] = []
    with db() as conn:
        mids = [r["member_id"] for r in conn.execute(
            "SELECT DISTINCT member_id FROM equity_points").fetchall()]
        for mid in mids:
            pts = conn.execute(
                "SELECT ts, total_value FROM equity_points WHERE member_id=? "
                "ORDER BY ts DESC LIMIT 2", (mid,)).fetchall()
            if len(pts) < 2:
                continue
            t2, v2 = pts[0]["ts"], float(pts[0]["total_value"] or 0)
            t1, v1 = pts[1]["ts"], float(pts[1]["total_value"] or 0)
            if v1 <= 0 or abs(v2 - v1) / v1 <= FLOW_STEP_PCT:
                continue
            traded = conn.execute(
                "SELECT 1 FROM executions WHERE member_id=? "
                "AND created_at BETWEEN ? AND ? LIMIT 1",
                (mid, t1 - FLOW_EXEC_SLACK_S, t2 + FLOW_EXEC_SLACK_S)).fetchone()
            if traded:
                continue
            name_row = conn.execute(
                "SELECT display_name FROM members WHERE id=?", (mid,)).fetchone()
            name = name_row["display_name"] if name_row else f"member {mid}"
            amount = round(v2 - v1, 2)
            conn.execute(
                "INSERT OR IGNORE INTO member_flows(member_id,ts,amount,kind,source,note) "
                "VALUES(?,?,?,?,?,?)",
                (mid, t2, amount, "suspected", "auto",
                 f"equity step ${v1:.2f} → ${v2:.2f} with no executions in the window"))
            out.append({"key": f"suspected-flow:{mid}:{t2}", "severity": "warn",
                        "message": (f"possible deposit/withdrawal ~${abs(amount):.2f} "
                                    f"for {name} — returns are computed around flows")})
    return out


# ─── W10: disk ───────────────────────────────────────────────────────────────

def _check_disk() -> list[dict]:
    st = os.statvfs(os.path.dirname(os.path.abspath(settings.db_path)) or "/")
    total = st.f_blocks * st.f_frsize
    if total <= 0:
        return []
    used_pct = 100.0 * (1.0 - (st.f_bavail * st.f_frsize) / total)
    if used_pct <= DISK_CRIT_PCT:
        return []
    return [{"key": "disk", "severity": "crit",
             "message": (f"disk holding the DB is {used_pct:.0f}% full — "
                         "WAL writes and backups are at risk")}]


# ─── W11: backup stale ───────────────────────────────────────────────────────

def _backup_dir() -> Path:
    """The backup job's output dir — read from jobs/backup.py itself (its
    _paths() is the authority), with the same env/default rules as a fallback
    if that import ever fails."""
    try:
        import importlib.util
        p = Path(__file__).resolve().parents[2] / "jobs" / "backup.py"
        spec = importlib.util.spec_from_file_location("_concord_backup_job", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._paths()[1]
    except Exception:  # noqa: BLE001
        src = Path(settings.db_path).expanduser().resolve()
        return Path(os.environ.get("CONCORD_BACKUP_DIR",
                                   str(src.parent.parent / "backups"))).expanduser()


def _check_backup_stale() -> list[dict]:
    d = _backup_dir()
    files = sorted(d.glob("concord-*.db"), key=lambda f: f.stat().st_mtime) \
        if d.is_dir() else []
    if not files:
        return [{"key": "backup-stale", "severity": "warn",
                 "message": (f"no backups found in {d} — the backup job may "
                             "never have run")}]
    age_h = (time.time() - files[-1].stat().st_mtime) / 3600
    if age_h <= BACKUP_STALE_H:
        return _check_offbox_stale()
    return [{"key": "backup-stale", "severity": "warn",
             "message": (f"newest backup ({files[-1].name}) is {age_h:.0f}h old "
                         f"(> {BACKUP_STALE_H}h) — check concord-backup.timer")}] \
        + _check_offbox_stale()


def _check_offbox_stale() -> list[dict]:
    """W11b: the off-box ship must be succeeding, not just the local write —
    same-disk-only backups are exactly the failure DR exists for. Only armed
    once a ship has EVER succeeded (or a dest is configured), so dev boxes
    with no off-box target stay quiet."""
    import os as _os

    from ..db import kv_get
    dest_set = bool(_os.environ.get("CONCORD_BACKUP_DEST", "").strip())
    stamp = kv_get("backup_offbox_ok_at")
    if not dest_set and not stamp:
        return []
    if not stamp:
        return [{"key": "backup-offbox-stale", "severity": "warn",
                 "message": "CONCORD_BACKUP_DEST is set but no off-box ship has "
                            "ever succeeded — check SSH key / dest path"}]
    age_h = (time.time() - int(stamp)) / 3600
    if age_h <= BACKUP_STALE_H:
        return []
    return [{"key": "backup-offbox-stale", "severity": "warn",
             "message": (f"last successful OFF-BOX backup ship was {age_h:.0f}h ago "
                         f"(> {BACKUP_STALE_H}h) — local backups continue but DR "
                         "is stale; check the ship target")}]


# ─── W12: underfunded stake ──────────────────────────────────────────────────

def _check_underfunded_stake() -> list[dict]:
    """A member's committed stake exceeds their account's total value (from the
    display snapshot) — the club can only deploy what's there. Also mails the
    member. Clears when funded (or the stake is lowered)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.display_name, m.email, s.alloc_type, s.alloc_value, "
            "snap.payload FROM members m "
            "JOIN member_settings s ON s.member_id=m.id AND s.opted_in=1 "
            "JOIN account_snapshots snap ON snap.member_id=m.id "
            "WHERE COALESCE(m.is_bot,0)=0 "
            f"AND {membership.active_clause('m')}").fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) or {}
            bal = (payload.get("allocation") or {}).get("balance") or {}
            tv = float(bal["total_value"])
        except Exception:  # noqa: BLE001 — no readable balance → nothing to judge
            continue
        stake = float(r["alloc_value"] or 0)
        if (r["alloc_type"] or "percent") != "dollar":
            stake = tv * stake / 100.0
        if stake <= tv + 0.01:
            continue
        body = (f"Hi {r['display_name']},\n\n"
                f"Your committed Concordia stake is ${stake:.2f}, but your "
                f"Robinhood account currently holds ${tv:.2f}.\n\n"
                "Add funds or lower your stake — the club can only deploy "
                "what's there.\n")
        out.append({
            "key": f"underfunded-stake:{r['id']}",
            "severity": "warn",
            "message": (f"{r['display_name']}'s stake (${stake:.2f}) exceeds their "
                        f"account value (${tv:.2f}) — add funds or lower the stake; "
                        "the club can only deploy what's there"),
            "notify": {"email": r["email"],
                       "subject": "Concordia: your stake exceeds your account balance",
                       "body": body},
        })
    return out


# ─── the sweep ───────────────────────────────────────────────────────────────

# (name, check). The name PREFIXES every key the check emits, which is what
# lets a crashed check keep its previously-announced alerts (never cleared on
# a crash — only on an observed recovery).
def _check_power_snapshot_stale() -> list[dict]:
    """W13 — voting power silently stopped updating.

    snapshot_power_all is deliberately fail-closed: on a degenerate roster it
    writes nothing and carries every member's prior power, which is right (it
    must never zero anyone's vote) but SILENT. Nothing consumed the
    `power_snapshot_degenerate` audit action, so a bail could freeze real-money
    voting weight indefinitely while the club kept trading on stale numbers —
    and it would not self-heal, because the next run hits the same condition.

    Fires on either signal: a degenerate bail more recent than the last
    successful snapshot, or no successful snapshot within the window. The window
    is generous (POWER_CADENCE can be biweekly) so this catches a stuck
    subsystem, not a quiet week.
    """
    if settings.power_model != "blend":
        return []
    now = int(time.time())
    window = 21 * 86400
    with db() as conn:
        ok = conn.execute(
            "SELECT MAX(ts) AS ts FROM audit_log WHERE action='blended_power'").fetchone()
        bad = conn.execute(
            "SELECT MAX(ts) AS ts FROM audit_log "
            "WHERE action='power_snapshot_degenerate'").fetchone()
    last_ok = int(ok["ts"] or 0) if ok else 0
    last_bad = int(bad["ts"] or 0) if bad else 0
    if last_bad and last_bad > last_ok:
        return [{"key": "power-snapshot-degenerate", "severity": "crit",
                 "message": ("voting power snapshot BAILED and has not succeeded since — "
                             "the club is trading on frozen voting weights"),
                 "since": last_bad}]
    if last_ok and now - last_ok > window:
        days = (now - last_ok) // 86400
        return [{"key": "power-snapshot-stale", "severity": "warn",
                 "message": f"no successful voting-power snapshot in {days} days",
                 "since": last_ok}]
    return []


_CHECKS: list[tuple] = [
    ("member-token-dead", _check_member_token_dead),                    # W1
    ("no-session-on-eve", _check_no_session_on_eve),                    # W2
    ("quorum-risk", _check_quorum_risk),                                # W3
    ("bots-idle", _check_bots_idle),                                    # W4
    ("halted-or-disarmed-on-cycle-morning", _check_halted_or_disarmed),  # W5
    ("missed-run", _check_missed_run),                                  # W6
    ("verify-failed", _check_verify_failed),                            # W7
    ("stuck-session", _check_stuck_session),                            # W8
    ("suspected-flow", _check_suspected_flow),                          # W9
    ("disk", _check_disk),                                              # W10
    ("backup-stale", _check_backup_stale),                              # W11
    ("underfunded-stake", _check_underfunded_stake),                    # W12
    ("power-snapshot", _check_power_snapshot_stale),                    # W13
]


def sweep() -> dict:
    """Run every check, announce NEW conditions once, clear recovered ones.

    Never raises past a check: each check is fenced (a broken check logs and
    moves on, and its existing alerts are left standing), delivery is fenced
    per email, and the kv/audit bookkeeping is fenced per finding.
    """
    findings: list[dict] = []
    crashed: set[str] = set()
    for name, fn in _CHECKS:
        try:
            findings.extend(fn() or [])
        except Exception as e:  # noqa: BLE001 — a broken check must not stop the sweep
            crashed.add(name)
            print(f"[watchdog] check '{name}' crashed: {type(e).__name__}: {e}",
                  flush=True)
    new: list[dict] = []
    active_keys: set[str] = set()
    now = int(time.time())
    for f in findings:
        try:
            key = _KV_PREFIX + f["key"]
            active_keys.add(key)
            if kv_get(key):
                continue                    # already announced; still active
            kv_set(key, json.dumps({"severity": f["severity"],
                                    "message": f["message"], "since": now}))
            audit("system", "watchdog_alert", f["key"],
                  {"severity": f["severity"], "message": f["message"]})
            new.append(f)
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] bookkeeping failed for {f.get('key')}: "
                  f"{type(e).__name__}: {e}", flush=True)
    # Clear kv for conditions no longer observed, so a recurrence re-alerts.
    # A crashed check couldn't observe anything — its keys are left standing.
    try:
        with db() as conn:
            standing = conn.execute(
                "SELECT key FROM kv WHERE key LIKE 'wd:%' AND value != ''").fetchall()
        for row in standing:
            key = row["key"]
            if key in active_keys:
                continue
            bare = key[len(_KV_PREFIX):]
            if any(bare == c or bare.startswith(c + ":") for c in crashed):
                continue
            kv_set(key, "")
    except Exception as e:  # noqa: BLE001
        print(f"[watchdog] clear pass failed: {type(e).__name__}: {e}", flush=True)
    try:
        _deliver(new)
    except Exception as e:  # noqa: BLE001 — alerting must never break the sweep
        print(f"[watchdog] delivery failed: {type(e).__name__}: {e}", flush=True)
    return {"findings": len(findings), "new": [f["key"] for f in new],
            "crashed": sorted(crashed)}


def _deliver(new: list[dict]) -> None:
    """One batched email per sweep to the admin roster + the per-member
    notifications (W1/W12). Email disabled → stdout. Per-email fencing."""
    if not new:
        return
    from html import escape

    from . import adminview, mailer
    subject = f"Concordia watchdog: {len(new)} new alert(s)"
    body = "\n".join(f"[{f['severity'].upper()}] {f['key']}: {f['message']}"
                     for f in new) + "\n"
    if not settings.email_enabled:
        print(f"[watchdog] email disabled — alerts below\nSubject: {subject}\n{body}",
              flush=True)
    else:
        html = f"<pre style=\"font-family:monospace\">{escape(body)}</pre>"
        for r in adminview.digest_recipients():
            try:
                mailer.send(r["email"], subject, html, body)
            except Exception:  # noqa: BLE001 — one bad send never blocks the rest
                pass
    for f in new:
        n = f.get("notify") or {}
        if not n.get("email"):
            continue
        if not settings.email_enabled:
            print(f"[watchdog] email disabled — member alert below\n"
                  f"To: {n['email']}\nSubject: {n['subject']}\n{n['body']}", flush=True)
            continue
        try:
            mailer.send(n["email"], n["subject"],
                        f"<pre style=\"font-family:monospace\">{escape(n['body'])}</pre>",
                        n["body"])
        except Exception:  # noqa: BLE001
            pass
