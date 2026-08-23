"""Tests for GET /api/admin/cycles — the per-cycle OPERATIONAL record.

Contract (the admin half of the member/admin split; the member half is pinned
in test_history_endpoints.py):
- Admin-only: plain member sessions 403, owner and admin-role sessions 200,
  unauthenticated 401 (require_admin, same gate as the rest of the console).
- Per decided cycle: {id, trade_date, status, status_display, basket,
  voters: {total, humans, bots}, orders: {<bucket>: n}, members: [...]}.
- HONEST buckets: a settled-cash defer is RECORDED as status='skipped' with
  error "deferred — beyond settled buying power" (executor.py) and must be
  aggregated as "deferred", not "skipped"; real skips (caps, no quote,
  blocking alerts) stay "skipped". The relabel is display/aggregation-level
  ONLY — the executions rows themselves are never rewritten.
- Per-member drilldown: name, placed/deferred counts, the deferred symbols +
  dollar amounts, skip and error reasons.

Run from backend/:  python -m pytest tests/test_admin_cycles.py -q
(members + sessions inserted directly, per-test spoofed IPs, same rationale as
test_admin_panel.py. Trade dates 2027-04-* are unique to this module — the
suite shares one DB and vote_sessions.trade_date is UNIQUE.)
"""
import json
import os
import tempfile
import time

# Point the app at a throwaway DB + dummy secrets BEFORE importing app modules.
os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
# Mirror the env the alphabetically-first module sets, so this module behaves
# the same alone as inside the full suite (one process-wide config singleton).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]

DEFER_ERR = "deferred — beyond settled buying power"   # executor.py's exact spelling


def setup_module(_):
    init_db()


def teardown_module(_):
    """Remove this module's sessions (votes + executions with them): the suite
    shares one DB and one process, this module sorts BEFORE test_admin_panel,
    and a leftover 2027-04-* row would otherwise become the suite's "latest
    executed" cycle / current open ballot and flip that module's will-place and
    last_executed assertions (same hygiene as test_history_endpoints' finally
    blocks)."""
    with db() as conn:
        sids = [r["id"] for r in conn.execute(
            "SELECT id FROM vote_sessions WHERE trade_date LIKE '2027-04-%'")]
        if sids:
            marks = ",".join("?" * len(sids))
            conn.execute(f"DELETE FROM votes WHERE session_id IN ({marks})", sids)  # noqa: S608
            conn.execute(f"DELETE FROM executions WHERE session_id IN ({marks})", sids)  # noqa: S608
            conn.execute(f"DELETE FROM vote_sessions WHERE id IN ({marks})", sids)  # noqa: S608


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    """Fresh spoofed client IP per call site (client_ip trusts the LAST XFF hop),
    so these tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.87.0.{_ip_counter[0]}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, role: str = "member", is_bot: int = 0) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], role, hash_password("cyc-pass-11"),
             now, is_bot, now),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)   # members.session_version defaults to 1


def _mk_session(trade_date: str, status: str, picks: list[str] | None = None) -> int:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,"
            "closed_at,top_n,picks_json) VALUES(?,?,?,?,?,?,?)",
            (trade_date, status, now, now + 3600,
             now if status in ("closed", "executed") else None, 3,
             json.dumps([{"symbol": s} for s in picks]) if picks is not None else None),
        )
        return cur.lastrowid


def _vote(sid: int, mid: int, symbol: str = "AAPL", weight: float = 100.0):
    with db() as conn:
        conn.execute(
            "INSERT INTO votes(session_id,member_id,symbol,weight,created_at) "
            "VALUES(?,?,?,?,?)", (sid, mid, symbol, weight, int(time.time())),
        )


def _exec_row(sid: int, mid: int, symbol: str, status: str,
              error: str | None = None, amount: float = 10.0):
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,"
            "dollar_amount,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, mid, symbol, "buy", "market", amount, status, error,
             int(time.time())),
        )


# ─── auth: the operational record is admin-only ──────────────────────────────

def test_cycles_requires_admin():
    _, member_tok = _mk_member("cyc-member@cclub.test")
    assert client.get("/api/admin/cycles", headers=_hdrs()).status_code == 401
    r = client.get("/api/admin/cycles", headers=_hdrs(member_tok))
    assert r.status_code == 403


def test_cycles_ok_for_owner_and_admin_roles():
    _, owner_tok = _mk_member("cyc-owner@cclub.test", role="owner")
    _, admin_tok = _mk_member("cyc-admin@cclub.test", role="admin")
    for tok in (owner_tok, admin_tok):
        r = client.get("/api/admin/cycles?limit=60", headers=_hdrs(tok))
        assert r.status_code == 200
        assert isinstance(r.json()["cycles"], list)


# ─── the split + honest buckets + drilldown ──────────────────────────────────

def test_cycles_voter_split_buckets_and_member_drilldown():
    _, admin_tok = _mk_member("cyc-ops@cclub.test", role="admin")
    mid_h1, _ = _mk_member("cyc-h1@cclub.test")            # human voter + orders
    mid_h2, _ = _mk_member("cyc-h2@cclub.test")            # human voter + orders
    mid_bot, _ = _mk_member("cyc-bot@cclub.test", is_bot=1)  # bot voter

    sid = _mk_session("2027-04-01", "executed", picks=["AAPL", "MSFT", "NVDA"])
    _vote(sid, mid_h1, "AAPL")
    _vote(sid, mid_h2, "MSFT")
    _vote(sid, mid_bot, "NVDA")

    _exec_row(sid, mid_h1, "AAPL", "placed", amount=50.0)
    _exec_row(sid, mid_h1, "MSFT", "skipped", error=DEFER_ERR, amount=42.5)
    _exec_row(sid, mid_h1, "NVDA", "skipped", error=DEFER_ERR, amount=7.49)
    _exec_row(sid, mid_h2, "AAPL", "placed", amount=20.0)
    _exec_row(sid, mid_h2, "MSFT", "skipped", error="max_orders cap reached")
    _exec_row(sid, mid_h2, "NVDA", "error", error="broker 500")

    r = client.get("/api/admin/cycles?limit=60", headers=_hdrs(admin_tok))
    assert r.status_code == 200
    by_id = {c["id"]: c for c in r.json()["cycles"]}
    c = by_id[sid]

    assert c["trade_date"] == "2027-04-01" and c["status"] == "executed"
    assert c["basket"] == ["AAPL", "MSFT", "NVDA"]
    # human-vs-bot split, plainly
    assert c["voters"] == {"total": 3, "humans": 2, "bots": 1}
    # HONEST buckets: the two defers leave "skipped" and land in "deferred";
    # the cap skip is a real skip; the broker error is an error
    assert c["orders"] == {"placed": 2, "deferred": 2, "skipped": 1, "error": 1}

    members = {m["member_id"]: m for m in c["members"]}
    h1, h2 = members[mid_h1], members[mid_h2]
    assert h1["name"] == "cyc-h1"
    assert (h1["placed"], h1["deferred"], h1["skipped"], h1["errors"]) == (1, 2, 0, 0)
    assert h1["deferred_orders"] == [{"symbol": "MSFT", "amount": 42.5},
                                     {"symbol": "NVDA", "amount": 7.49}]
    assert (h2["placed"], h2["deferred"], h2["skipped"], h2["errors"]) == (1, 0, 1, 1)
    assert h2["skips"] == [{"symbol": "MSFT", "reason": "max_orders cap reached"}]
    assert h2["error_orders"] == [{"symbol": "NVDA", "reason": "broker 500"}]

    # the relabel is aggregation-level ONLY — the recorded rows are untouched
    with db() as conn:
        raw = conn.execute(
            "SELECT status, error FROM executions WHERE session_id=? AND error=?",
            (sid, DEFER_ERR)).fetchall()
    assert len(raw) == 2 and all(x["status"] == "skipped" for x in raw)


def test_cycles_excludes_open_sessions_and_clamps_limit():
    _, admin_tok = _mk_member("cyc-ops2@cclub.test", role="admin")
    s_open = _mk_session("2027-04-02", "open")
    s_closed = _mk_session("2027-04-03", "closed", picks=["KO"])

    r = client.get("/api/admin/cycles?limit=500", headers=_hdrs(admin_tok))
    assert r.status_code == 200
    cycles = r.json()["cycles"]
    ids = {c["id"] for c in cycles}
    assert s_closed in ids and s_open not in ids
    assert len(cycles) <= 60
    one = client.get("/api/admin/cycles?limit=1", headers=_hdrs(admin_tok))
    assert len(one.json()["cycles"]) == 1
