"""Member trade receipts (services/notify.py) — the G5a notification layer.

Contract:
- LIVE runs only (will_place true), gated by settings.member_run_emails.
- Each HUMAN member with ≥1 meaningful order row gets ONE email: placed
  buys/sells, deferred buys in plain English (with the $ total from the
  executions ledger when the session is known), error rows, and a
  taxable-events sentence only when sells placed. A stranded (rh error)
  member gets the reconnect call-to-action instead. A member with nothing
  to report gets NOTHING.
- A commitment write-down (commit_clamped) is stated outright — both figures
  and the reason — and earns a receipt even with no orders behind it; a carry
  drain (carry_drained) rides with the buys it funded. Both come from the
  member's OWN summary row, never another's.
- email_enabled=False → each receipt prints to stdout.
- Constitutionally incapable of hurting a run: executor.run survives
  notify raising outright.

Run from backend/:  python -m pytest tests/test_notify.py -q
"""
import json
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.services import executor, mailer, notify  # noqa: E402


def setup_module(_):
    init_db()


def teardown_module(_):
    # House pattern (see test_audit_fixes): shared temp DB — reset the mutable
    # tables so this module's members/sessions don't leak into later modules.
    with db() as conn:
        for t in ("executions", "votes", "bot_picks", "vote_sessions",
                  "voting_power_history", "login_events", "api_keys", "audit_log",
                  "rh_accounts", "member_settings", "members", "run_reports"):
            conn.execute(f"DELETE FROM {t}")


def _member(email: str, is_bot: int = 0) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,is_bot,created_at) "
            "VALUES(?,?,'member',?,?)",
            (email, email.split("@")[0], is_bot, int(time.time())))
    return cur.lastrowid


def _summary(members: list[dict], trade_date: str = "2028-05-01",
             will_place: bool = True) -> dict:
    return {"ok": True, "will_place": will_place, "trade_date": trade_date,
            "phase": "both", "members": members, "orders": []}


def _mrow(mid: int, orders: list[dict], skip_reason: str | None = None) -> dict:
    row = {"member_id": mid, "name": f"m{mid}", "orders": orders}
    if skip_reason:
        row["skip_reason"] = skip_reason
    return row


# ─── email-off stdout path + body content ────────────────────────────────────

def test_receipt_prints_full_story_when_email_disabled(capsys):
    mid = _member("rcpt-full@nt.test")
    orders = [
        {"symbol": "AAPL", "side": "buy", "status": "placed", "amount": "50.00"},
        {"symbol": "TSLA", "side": "sell", "status": "placed", "qty": "1.000000",
         "note": "exited basket"},
        {"symbol": "MSFT", "side": "buy", "status": "skipped",
         "note": "defer · settles next cycle"},
        {"symbol": "NVDA", "side": "buy", "status": "error", "note": "boom"},
    ]
    res = notify.send_run_emails(_summary([_mrow(mid, orders)]))
    out = capsys.readouterr().out
    assert "To: rcpt-full@nt.test" in out
    assert "Concordia traded for you — 2028-05-01" in out
    assert "BUY   AAPL  $50.00" in out
    assert "SELL  TSLA  1.000000 shares  (exited basket)" in out
    # no session row for this date → the count phrasing, still plain English
    assert "1 buy(s) wait for settled cash" in out
    assert "next top-up day" in out
    assert "retry automatically next run" in out
    assert "taxable" in out
    assert res["printed"] == ["rcpt-full@nt.test"]
    assert res["sent"] == [] and res["failed"] == []


def test_deferred_dollars_come_from_the_executions_ledger(capsys):
    mid = _member("rcpt-defer@nt.test")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,top_n) "
            "VALUES('2028-05-02','closed',3)")
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,"
            "dollar_amount,ref_id,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, mid, "MSFT", "buy", "market", 123.45, f"nt:{sid}:{mid}:MSFT",
             "skipped", "deferred — beyond settled buying power", int(time.time())))
    orders = [{"symbol": "MSFT", "side": "buy", "status": "skipped",
               "note": "defer · settles next cycle"}]
    notify.send_run_emails(_summary([_mrow(mid, orders)], trade_date="2028-05-02"))
    out = capsys.readouterr().out
    assert "$123.45 of buys wait for settled cash" in out


def test_taxable_sentence_only_when_sells_placed(capsys):
    mid = _member("rcpt-notax@nt.test")
    orders = [{"symbol": "AAPL", "side": "buy", "status": "placed", "amount": "25.00"}]
    notify.send_run_emails(_summary([_mrow(mid, orders)]))
    out = capsys.readouterr().out
    assert "BUY   AAPL" in out
    assert "taxable" not in out


# ─── the commitment write-down + the carry drain ─────────────────────────────
# The executor clamps a committed stake down to what the account can actually
# deploy (msum["commit_clamped"]) and folds an earlier cycle's undeployed
# dollars back into this one (msum["carry_drained"]). Both are the member's own
# money: the receipt says so, and says it with THIS member's figures only.

def test_clamp_is_stated_with_both_figures(capsys):
    mid = _member("rcpt-clamp@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                       "amount": "50.00"}])
    row["commit_clamped"] = {"from": 1984.0, "to": 720.5}
    notify.send_run_emails(_summary([row]))
    out = capsys.readouterr().out
    assert "$720.50" in out and "$1,984.00" in out
    assert "committed amount" in out
    assert "deploy" in out, "the receipt says WHY it was reduced"
    assert "Concordia traded for you — 2028-05-01" in out, \
        "a run that also traded keeps the trade subject"


def test_clamp_alone_earns_a_receipt_of_its_own(capsys):
    mid = _member("rcpt-clamp-only@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "held",
                       "note": "at target"}])
    row["commit_clamped"] = {"from": 300.0, "to": 120.0}
    res = notify.send_run_emails(_summary([row]))
    out = capsys.readouterr().out
    assert res["printed"] == ["rcpt-clamp-only@nt.test"]
    assert "reduced your committed amount" in out, \
        "no orders → the subject must not claim a trade"
    assert "traded for you" not in out
    assert "$120.00" in out and "$300.00" in out


def test_drain_rides_with_the_buys_it_funded(capsys):
    mid = _member("rcpt-drain@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                       "amount": "45.00"}])
    row["carry_drained"] = 25.0
    notify.send_run_emails(_summary([row]))
    out = capsys.readouterr().out
    assert "$25.00 an earlier cycle couldn't deploy" in out
    assert "back to work" in out


def test_drain_with_every_buy_deferred_claims_no_buy(capsys):
    """The drain funds the slice, not an order: with nothing placed the receipt
    must not say the dollars went into buys this run never made."""
    mid = _member("rcpt-drain-defer@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "skipped",
                       "note": "defer · settles next cycle"}])
    row["carry_drained"] = 25.0
    notify.send_run_emails(_summary([row]))
    out = capsys.readouterr().out
    assert "$25.00 an earlier cycle couldn't deploy" in out
    assert "back to work in these buys" not in out
    assert "this cycle's budget" in out


def test_drain_without_orders_is_not_a_receipt(capsys):
    mid = _member("rcpt-drain-quiet@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "held",
                       "note": "at target"}])
    row["carry_drained"] = 25.0
    res = notify.send_run_emails(_summary([row]))
    assert res["printed"] == []
    assert "rcpt-drain-quiet@nt.test" not in capsys.readouterr().out


def test_a_clamp_never_leaks_into_another_members_receipt(monkeypatch):
    a = _member("rcpt-clamp-a@nt.test")
    b = _member("rcpt-clamp-b@nt.test")
    object.__setattr__(settings, "resend_api_key", "test-key")   # conftest restores
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, html, text: sent.append((to, text)) or True)
    rowa = _mrow(a, [{"symbol": "AAPL", "side": "buy", "status": "placed", "amount": "10.00"}])
    rowa["commit_clamped"] = {"from": 900.0, "to": 410.0}
    rowa["carry_drained"] = 31.25
    rowb = _mrow(b, [{"symbol": "MSFT", "side": "buy", "status": "placed", "amount": "20.00"}])
    notify.send_run_emails(_summary([rowa, rowb]))
    by_to = dict(sent)
    assert "$410.00" in by_to["rcpt-clamp-a@nt.test"]
    for figure in ("$410.00", "$900.00", "$31.25"):
        assert figure not in by_to["rcpt-clamp-b@nt.test"], \
            "one member's money figures never appear in another's receipt"


def test_malformed_clamp_says_nothing_rather_than_guessing(capsys):
    mid = _member("rcpt-clamp-bad@nt.test")
    row = _mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                       "amount": "10.00"}])
    row["commit_clamped"] = {"from": None}           # shape the executor never writes
    res = notify.send_run_emails(_summary([row]))
    out = capsys.readouterr().out
    assert res["printed"] == ["rcpt-clamp-bad@nt.test"], "the trade receipt still goes"
    assert "committed amount" not in out


# ─── who gets an email (and who never does) ──────────────────────────────────

def test_member_with_zero_orders_gets_no_email(capsys):
    quiet = _member("rcpt-quiet@nt.test")
    busy = _member("rcpt-busy@nt.test")
    res = notify.send_run_emails(_summary([
        _mrow(quiet, []),
        _mrow(busy, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                      "amount": "10.00"}]),
    ]))
    out = capsys.readouterr().out
    assert "rcpt-busy@nt.test" in out
    assert "rcpt-quiet@nt.test" not in out
    assert res["printed"] == ["rcpt-busy@nt.test"]


def test_held_only_member_gets_no_email(capsys):
    mid = _member("rcpt-held@nt.test")
    orders = [{"symbol": "AAPL", "side": "buy", "status": "held",
               "note": "at target"}]
    notify.send_run_emails(_summary([_mrow(mid, orders)]))
    assert "rcpt-held@nt.test" not in capsys.readouterr().out


def test_stranded_member_gets_reconnect_cta_instead(capsys):
    mid = _member("rcpt-stranded@nt.test")
    notify.send_run_emails(_summary([_mrow(mid, [], skip_reason="rh_error_stranded")]))
    out = capsys.readouterr().out
    assert "To: rcpt-stranded@nt.test" in out
    assert "reconnect" in out.lower()
    assert "traded for you" not in out


def test_bots_are_never_emailed(capsys):
    bot = _member("rcpt-bot@nt.test", is_bot=1)
    notify.send_run_emails(_summary([
        _mrow(bot, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                     "amount": "10.00"}])]))
    assert "rcpt-bot@nt.test" not in capsys.readouterr().out


# ─── gates ───────────────────────────────────────────────────────────────────

def test_dry_runs_send_nothing(capsys):
    mid = _member("rcpt-dry@nt.test")
    res = notify.send_run_emails(_summary(
        [_mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "reviewed"}])],
        will_place=False))
    assert res["skipped"] == "not a live run"
    assert "rcpt-dry@nt.test" not in capsys.readouterr().out


def test_member_run_emails_knob_turns_it_off(capsys):
    object.__setattr__(settings, "member_run_emails", False)   # conftest restores
    mid = _member("rcpt-off@nt.test")
    res = notify.send_run_emails(_summary(
        [_mrow(mid, [{"symbol": "AAPL", "side": "buy", "status": "placed",
                      "amount": "10.00"}])]))
    assert res["skipped"] == "member_run_emails disabled"
    assert "rcpt-off@nt.test" not in capsys.readouterr().out


# ─── per-member routing + fencing with real email enabled ────────────────────

def test_each_member_routed_their_own_receipt(monkeypatch):
    a = _member("rcpt-route-a@nt.test")
    b = _member("rcpt-route-b@nt.test")
    object.__setattr__(settings, "resend_api_key", "test-key")   # conftest restores
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, html, text: sent.append((to, subject, text)) or True)
    res = notify.send_run_emails(_summary([
        _mrow(a, [{"symbol": "AAPL", "side": "buy", "status": "placed", "amount": "10.00"}]),
        _mrow(b, [{"symbol": "MSFT", "side": "buy", "status": "placed", "amount": "20.00"}]),
    ]))
    assert sorted(res["sent"]) == ["rcpt-route-a@nt.test", "rcpt-route-b@nt.test"]
    by_to = {to: text for to, _s, text in sent}
    assert "AAPL" in by_to["rcpt-route-a@nt.test"]
    assert "MSFT" not in by_to["rcpt-route-a@nt.test"], "receipts are per-member"
    assert "MSFT" in by_to["rcpt-route-b@nt.test"]
    assert all(s == "Concordia traded for you — 2028-05-01" for _t, s, _x in sent)


def test_one_raising_send_never_blocks_the_next_member(monkeypatch):
    a = _member("rcpt-fence-a@nt.test")
    b = _member("rcpt-fence-b@nt.test")
    object.__setattr__(settings, "resend_api_key", "test-key")
    sent: list[str] = []

    def _send(to, subject, html, text):
        if to.startswith("rcpt-fence-a"):
            raise RuntimeError("smtp down")
        sent.append(to)
        return True
    monkeypatch.setattr(mailer, "send", _send)
    res = notify.send_run_emails(_summary([
        _mrow(a, [{"symbol": "AAPL", "side": "buy", "status": "placed", "amount": "10.00"}]),
        _mrow(b, [{"symbol": "MSFT", "side": "buy", "status": "placed", "amount": "20.00"}]),
    ]))
    assert res["failed"] == ["rcpt-fence-a@nt.test"]
    assert res["sent"] == ["rcpt-fence-b@nt.test"] == sent


# ─── the executor call site: a run can NEVER fail on notification ────────────

def test_run_survives_notify_raising(monkeypatch):
    def _boom(_s):
        raise RuntimeError("notify exploded")
    monkeypatch.setattr(notify, "send_run_emails", _boom)
    session = {"id": 987001, "status": "closed", "trade_date": "2028-05-03",
               "picks_json": "[]"}
    out = executor.run(session, force_dry_run=True)
    assert out["ok"] is True and "no consensus picks" in out["note"]


def test_call_site_hands_notify_the_persisted_report(monkeypatch):
    got: dict = {}

    def _spy(s):
        # Snapshot the ledger AT CALL TIME: the report row must already exist
        # when notify is invoked (call site is strictly after persistence).
        with db() as conn:
            row = conn.execute("SELECT summary_json FROM run_reports "
                               "ORDER BY id DESC LIMIT 1").fetchone()
        got["report_at_call"] = json.loads(row["summary_json"]) if row else None
        got.update(s)
        return {}
    monkeypatch.setattr(notify, "send_run_emails", _spy)
    session = {"id": 987002, "status": "closed", "trade_date": "2028-05-04",
               "picks_json": "[]"}
    executor.run(session, force_dry_run=True, trigger="manual:t@nt.test")
    assert got.get("trade_date") == "2028-05-04", \
        "notify sees the report dict (identity fields backfilled)"
    assert got["report_at_call"] and \
        got["report_at_call"].get("trade_date") == "2028-05-04", \
        "the report row is already persisted WHEN notify is invoked"
