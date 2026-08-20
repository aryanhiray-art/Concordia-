"""Tests for RECORDED account-value history (services/equity_history).

Why it exists: the member's Overview account line was drawn from
voting_power_history.equity — written once a WEEK — and interpolated in the
browser, so nearly every point on that curve was invented. Robinhood exposes no
portfolio-history endpoint, so the only fix is to record the value going
forward, on the balance read the 10-minute snapshot refresher already does.

Contract under test:
- a SUCCESSFUL snapshot refresh records exactly one real point
- a FAILED refresh (and a None / missing balance) records NOTHING — a gap is
  honest, a fake $0 on a money chart is not
- retention thins points older than the fine window to one per day and leaves
  recent points completely untouched
- the weekly-history seed is idempotent (row-level AND run-once)
- each range returns only points inside its window, downsampled to REAL points
- a member with no history gets an honest empty payload, not a 500

Run from backend/:  python -m pytest tests/test_equity_history.py -q
"""
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402
from app.services import accountsnap, alloc, equity_history, positions  # noqa: E402

client = TestClient(app)

_ip_counter = [0]
DAY = 86_400

_BALANCE = {"total_value": 1000.0, "cash": 250.0, "buying_power": 250.0}
_HOLDINGS = [{"symbol": "AAPL", "quantity": 2.0, "value": 400.0, "pnl": 40.0}]


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hdrs(tok: str | None = None) -> dict:
    _ip_counter[0] += 1
    h = {"X-Forwarded-For": f"10.83.0.{_ip_counter[0] % 250}"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _mk_member(email: str, connected: bool = True) -> tuple[int, str]:
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO members(email,display_name,role,password_hash,email_verified_at,"
            "is_bot,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, email.split("@")[0], "member", hash_password("equity-pass-11"), now, 0, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
            "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
            "VALUES(?,1,0,0,?,?,'dollar',100.0,?)",
            (mid, settings.max_orders, settings.voting_power_base, now),
        )
        if connected:
            conn.execute(
                "INSERT INTO rh_accounts(member_id,account_number,agentic_allowed,status,"
                "connected_at) VALUES(?,?,?,?,?)", (mid, f"ACC{mid}", 1, "connected", now))
    return mid, make_session(mid, 1)


class _Broker:
    def __init__(self):
        self.balance = dict(_BALANCE)
        self.holdings = [dict(h) for h in _HOLDINGS]
        self.boom = False

    def member_balance(self, member_id: int):
        if self.boom:
            raise RuntimeError("robinhood is down")
        # Same contract as the real alloc.member_balance: None when the member
        # has no connected Robinhood account.
        with db() as conn:
            row = conn.execute(
                "SELECT 1 FROM rh_accounts WHERE member_id=? AND status='connected'",
                (member_id,)).fetchone()
        if not row or self.balance is None:
            return None
        return dict(self.balance)

    def holdings_with_value(self, member_id: int):
        if self.boom:
            raise RuntimeError("robinhood is down")
        return [dict(h) for h in self.holdings]


def _wire_broker(monkeypatch) -> _Broker:
    b = _Broker()
    monkeypatch.setattr(alloc, "member_balance", b.member_balance)
    monkeypatch.setattr(positions, "holdings_with_value", b.holdings_with_value)
    return b


def _points(member_id: int) -> list[tuple[int, float]]:
    with db() as conn:
        return [(int(r["ts"]), float(r["total_value"])) for r in conn.execute(
            "SELECT ts,total_value FROM equity_points WHERE member_id=? ORDER BY ts",
            (member_id,)).fetchall()]


def _put(member_id: int, ts: int, value: float) -> None:
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO equity_points(member_id,ts,total_value) "
                     "VALUES(?,?,?)", (member_id, ts, value))


# ─── the write: only real, successful reads ──────────────────────────────────

def test_successful_refresh_records_one_real_point(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, _ = _mk_member("eq-record@aclub.test")

    row = accountsnap.refresh(mid)
    assert row["ok"] is True
    pts = _points(mid)
    assert len(pts) == 1
    assert pts[0][1] == 1000.0
    assert abs(pts[0][0] - int(time.time())) < 30

    # a later refresh at a new value appends, it does not overwrite history
    b.balance["total_value"] = 1234.56
    time.sleep(1.1)                       # ts has 1-second resolution
    accountsnap.refresh(mid)
    pts = _points(mid)
    assert len(pts) == 2 and pts[-1][1] == 1234.56


def test_failed_broker_read_records_nothing(monkeypatch):
    b = _wire_broker(monkeypatch)
    mid, _ = _mk_member("eq-boom@aclub.test")
    accountsnap.refresh(mid)
    assert len(_points(mid)) == 1

    b.boom = True
    for _ in range(3):
        assert accountsnap.refresh(mid)["ok"] is False
    assert len(_points(mid)) == 1, "a failed read must not write a point (never a fake $0)"
    assert all(v > 0 for _, v in _points(mid))


def test_none_balance_records_nothing(monkeypatch):
    b = _wire_broker(monkeypatch)

    # not connected to Robinhood at all → balance is None
    off_mid, _ = _mk_member("eq-noconnect@aclub.test", connected=False)
    assert accountsnap.refresh(off_mid)["ok"] is True
    assert _points(off_mid) == []

    # connected, but the portfolio came back without a usable total_value
    mid, _ = _mk_member("eq-nonevalue@aclub.test")
    b.balance = {"total_value": None, "cash": None, "buying_power": None}
    assert accountsnap.refresh(mid)["ok"] is True
    assert _points(mid) == []

    # and the guard itself rejects the bad shapes outright
    assert equity_history.record(mid, None) is False
    assert equity_history.record(mid, float("nan")) is False
    assert equity_history.record(mid, float("inf")) is False
    assert equity_history.record(mid, -5.0) is False
    assert equity_history.record(mid, "not a number") is False
    assert _points(mid) == []
    # a genuinely empty account IS zero, and that is a real observation
    assert equity_history.record(mid, 0.0) is True
    assert _points(mid) == [(_points(mid)[0][0], 0.0)]


# ─── retention ───────────────────────────────────────────────────────────────

def test_prune_thins_old_points_to_one_a_day_and_keeps_recent_ones():
    mid, _ = _mk_member("eq-prune@aclub.test")
    now = int(time.time())

    # 6 fine-grained points inside the last 2 days: all must survive
    recent = [now - i * 3600 for i in range(6)]
    for i, ts in enumerate(recent):
        _put(mid, ts, 100.0 + i)
    # 3 old days x 4 points each, well outside the fine window.
    # ALIGNED to the prune's own day buckets (UTC midnight), not to `now`.
    # Anchoring to `now` made this test depend on the wall-clock hour: a "day"
    # of points spanning base-3h..base straddles a bucket boundary whenever the
    # run happens within 3 hours of UTC midnight, so the survivor landed in a
    # neighbouring bucket and the assertion failed only at certain times of day.
    _b = equity_history.DAY_BUCKET_SECS
    old_days = [((now - d * DAY) // _b) * _b + _b - 60 for d in (10, 11, 12)]
    for d, base in enumerate(old_days):
        for i in range(4):
            _put(mid, base - i * 3600, 50.0 + d * 10 + i)
    assert len(_points(mid)) == 18

    equity_history.prune()
    kept = _points(mid)
    kept_ts = {t for t, _ in kept}
    assert set(recent) <= kept_ts, "points inside the fine window are untouched"

    old_kept = [t for t in kept_ts if t < now - equity_history.FINE_WINDOW_DAYS * DAY]
    buckets = {t // equity_history.DAY_BUCKET_SECS for t in old_kept}
    assert len(old_kept) == len(buckets), "at most one old point per day bucket"
    assert len(old_kept) >= 3, "each old day keeps its last real point"
    # and what survived is the LAST observation of its day, not a synthesised one
    for base in old_days:
        day = base // equity_history.DAY_BUCKET_SECS
        same = [t for t in old_kept if t // equity_history.DAY_BUCKET_SECS == day]
        assert same == [base]

    # idempotent: a second prune has nothing left to do
    assert equity_history.prune() == 0
    assert _points(mid) == kept


# ─── seed from the weekly voting-power history ───────────────────────────────

def test_seed_from_voting_power_is_idempotent():
    mid, _ = _mk_member("eq-seed@aclub.test")
    now = int(time.time())
    rows = [("2026-07-05", 900.0, now - 26 * DAY),
            ("2026-07-12", 950.0, now - 19 * DAY),
            ("2026-07-19", 980.0, now - 12 * DAY),
            ("2026-07-26", None,  now - 5 * DAY)]   # no equity recorded → skipped
    with db() as conn:
        for week, equity, created in rows:
            conn.execute(
                "INSERT INTO voting_power_history(member_id,week_start,power,equity,"
                "created_at) VALUES(?,?,?,?,?)", (mid, week, 100.0, equity, created))

    equity_history.seed_from_voting_power(force=True)
    seeded = _points(mid)
    assert seeded == [(now - 26 * DAY, 900.0), (now - 19 * DAY, 950.0),
                      (now - 12 * DAY, 980.0)]

    # running it again inserts nothing and duplicates nothing
    again = equity_history.seed_from_voting_power(force=True)
    assert again["inserted"] == 0
    assert _points(mid) == seeded

    # and the run-once flag makes the unforced call a no-op
    guarded = equity_history.seed_from_voting_power()
    assert guarded["seeded"] is False and guarded["reason"] == "already_seeded"


# ─── the endpoint ────────────────────────────────────────────────────────────

def test_each_range_returns_only_points_inside_its_window():
    mid, tok = _mk_member("eq-ranges@aclub.test")
    now = int(time.time())
    ages = {"30m": now - 1800, "3d": now - 3 * DAY, "20d": now - 20 * DAY,
            "60d": now - 60 * DAY, "200d": now - 200 * DAY, "500d": now - 500 * DAY}
    for i, ts in enumerate(sorted(ages.values())):
        _put(mid, ts, 500.0 + i)

    # 1D is deliberately NOT a trailing-24h window any more — it serves the
    # last ET trading session that has points (see the dedicated weekend
    # tests below), so it is excluded from the fixed-window sweep.
    expected = {"1W": 2, "1M": 3, "3M": 4, "1Y": 5, "ALL": 6}
    for rng, count in expected.items():
        r = client.get(f"/api/me/equity?range={rng}", headers=_hdrs(tok))
        assert r.status_code == 200, rng
        body = r.json()
        assert set(body) == {"range", "points", "resolution", "first_ts", "ok"}
        assert body["range"] == rng
        assert len(body["points"]) == count, rng
        assert body["first_ts"] == ages["500d"], "first_ts spans ALL history, not the range"
        assert body["ok"] is (count >= 2)
        ts_list = [p["t"] for p in body["points"]]
        assert ts_list == sorted(ts_list)
        window = equity_history.RANGE_DAYS[rng]
        if window is not None:
            assert min(ts_list) >= now - window * DAY - 5

    assert client.get("/api/me/equity?range=6M", headers=_hdrs(tok)).status_code == 400
    assert client.get("/api/me/equity", headers=_hdrs(tok)).json()["range"] == \
        equity_history.DEFAULT_RANGE


def test_long_range_is_downsampled_to_real_points_only():
    mid, tok = _mk_member("eq-downsample@aclub.test")
    now = int(time.time())
    recorded = {now - i * 60: 700.0 + i * 0.25 for i in range(1000)}   # ~16h of 1-min points
    for ts, v in recorded.items():
        _put(mid, ts, v)

    body = client.get("/api/me/equity?range=1W", headers=_hdrs(tok)).json()
    pts = body["points"]
    assert 2 < len(pts) <= equity_history.MAX_POINTS
    assert len(pts) < 1000 and body["resolution"] != "raw"
    for p in pts:
        assert recorded.get(p["t"]) == p["v"], "downsampling must never invent a value"
    assert pts[-1]["t"] == max(recorded), "the newest observation is always kept"

    # a small series is served untouched
    small, tok2 = _mk_member("eq-raw@aclub.test")
    for i in range(5):
        _put(small, now - i * 600, 300.0 + i)
    raw = client.get("/api/me/equity?range=1W", headers=_hdrs(tok2)).json()
    assert raw["resolution"] == "raw" and len(raw["points"]) == 5 and raw["ok"] is True


def test_member_with_no_history_gets_an_honest_empty_payload():
    _, tok = _mk_member("eq-empty@aclub.test")
    r = client.get("/api/me/equity?range=ALL", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert body == {"range": "ALL", "points": [], "resolution": "raw",
                    "first_ts": None, "ok": False}

    # one lone point is returned honestly, still not enough to draw a line
    mid2, tok2 = _mk_member("eq-onepoint@aclub.test")
    ts = int(time.time()) - 120
    _put(mid2, ts, 42.0)
    body2 = client.get("/api/me/equity?range=1W", headers=_hdrs(tok2)).json()
    assert body2["points"] == [{"t": ts, "v": 42.0}]
    assert body2["ok"] is False and body2["first_ts"] == ts


def test_equity_endpoint_requires_a_member():
    assert client.get("/api/me/equity", headers=_hdrs()).status_code in (401, 403)


def _shape(monkeypatch, day_index: dict):
    """Pin the club shape the densifier sees."""
    monkeypatch.setattr(equity_history, "_club_shape", lambda: dict(day_index))


def test_owner_example_110_backfills_along_the_club_curve(monkeypatch):
    """One real $110 observation after a +10% club run must draw 100 -> 110
    along the club's path — not appear out of nowhere."""
    import datetime as _dt

    from app.calendar_us import ET

    mid, tok = _mk_member("densify-110@aclub.test")
    _shape(monkeypatch, {"2026-07-20": 100.0, "2026-07-21": 104.0,
                         "2026-07-22": 107.0, "2026-07-23": 110.0})
    obs = int(_dt.datetime(2026, 7, 23, 16, 0, tzinfo=ET).timestamp())
    equity_history.record(mid, 110.0, ts=obs)

    now = int(_dt.datetime(2026, 7, 24, 12, 0, tzinfo=ET).timestamp())
    out = equity_history.series(mid, "1W", now=now)
    pts = out["points"]
    assert [round(p["v"], 2) for p in pts] == [100.0, 104.0, 107.0, 110.0]
    assert [bool(p.get("est")) for p in pts] == [True, True, True, False]
    assert out["ok"] is True


def test_gaps_between_anchors_follow_the_club_and_land_on_the_real_value(monkeypatch):
    """Estimates take the club's shape but must end EXACTLY on the next real
    observation — anchors are the truth, the shape is only the path."""
    import datetime as _dt

    from app.calendar_us import ET

    mid, tok = _mk_member("densify-gap@aclub.test")
    _shape(monkeypatch, {"2026-07-20": 100.0, "2026-07-21": 110.0,
                         "2026-07-22": 120.0, "2026-07-23": 130.0})
    t0 = int(_dt.datetime(2026, 7, 20, 16, 0, tzinfo=ET).timestamp())
    t1 = int(_dt.datetime(2026, 7, 23, 16, 0, tzinfo=ET).timestamp())
    equity_history.record(mid, 200.0, ts=t0)
    # the member's balance did NOT track the club exactly: ends at 250, not 260
    equity_history.record(mid, 250.0, ts=t1)

    now = t1 + 3600
    pts = equity_history.series(mid, "1W", now=now)["points"]
    assert pts[0]["v"] == 200.0 and not pts[0].get("est")
    assert pts[-1]["v"] == 250.0 and not pts[-1].get("est")
    mids = [p for p in pts if p.get("est")]
    assert len(mids) == 2
    # club rose monotonically, so the estimated path must too, staying between
    # the anchors rather than overshooting either
    vals = [p["v"] for p in pts]
    assert vals == sorted(vals)
    assert all(200.0 < p["v"] < 250.0 for p in mids)


def test_days_with_real_observations_are_never_overridden(monkeypatch):
    import datetime as _dt

    from app.calendar_us import ET

    mid, tok = _mk_member("densify-real@aclub.test")
    _shape(monkeypatch, {"2026-07-20": 100.0, "2026-07-21": 150.0,
                         "2026-07-22": 100.0})
    for day, v in (("2026-07-20", 500.0), ("2026-07-21", 501.0), ("2026-07-22", 502.0)):
        y, m, d = (int(x) for x in day.split("-"))
        equity_history.record(mid, v, ts=int(_dt.datetime(y, m, d, 16, 0, tzinfo=ET).timestamp()))
    now = int(_dt.datetime(2026, 7, 22, 17, 0, tzinfo=ET).timestamp())
    pts = equity_history.series(mid, "1W", now=now)["points"]
    assert [p["v"] for p in pts] == [500.0, 501.0, 502.0]
    assert not any(p.get("est") for p in pts), "no estimates when every day is real"


def test_no_club_shape_means_no_invented_points(monkeypatch):
    """Without a club record there is nothing honest to shape a gap with —
    sparse stays sparse rather than inventing a path."""
    import datetime as _dt

    from app.calendar_us import ET

    mid, tok = _mk_member("densify-noshape@aclub.test")
    _shape(monkeypatch, {})
    t0 = int(_dt.datetime(2026, 7, 20, 16, 0, tzinfo=ET).timestamp())
    equity_history.record(mid, 100.0, ts=t0)
    equity_history.record(mid, 120.0, ts=t0 + 5 * 86400)
    pts = equity_history.series(mid, "1W", now=t0 + 6 * 86400)["points"]
    assert len(pts) == 2 and not any(p.get("est") for p in pts)


def test_1d_range_returns_raw_last_day(monkeypatch):
    """1D is BACK (owner ask, 2026-08-20): the recorder has kept raw 10-minute
    points since FINE_WINDOW_DAYS landed, so a 1-day window slices real
    intraday observations — and only the last day's."""
    mid, tok = _mk_member("yes-1d@aclub.test")
    now = int(time.time())
    # two days of observations, a few per day
    for off, v in [(2 * 86400, 100.0), (2 * 86400 - 600, 101.0),
                   (3600, 110.0), (1800, 111.0), (600, 112.0)]:
        equity_history.record(mid, v, ts=now - off)
    r = client.get("/api/me/equity?range=1D", headers=_hdrs(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    vals = [p["v"] for p in body["points"]]
    assert vals == [110.0, 111.0, 112.0]      # only the last 24h, oldest first


def test_weekend_anchor_still_backfills(monkeypatch):
    """The first real recording happened on a SATURDAY; the club series ends
    Friday. The anchor must borrow Friday's index, not silently disable all
    backfill (the original bug: exact-day lookup -> zero estimates)."""
    import datetime as _dt

    from app.calendar_us import ET

    mid, tok = _mk_member("densify-weekend@aclub.test")
    _shape(monkeypatch, {"2026-07-30": 100.0, "2026-07-31": 110.0})
    sat = int(_dt.datetime(2026, 8, 1, 22, 39, tzinfo=ET).timestamp())
    equity_history.record(mid, 342.30, ts=sat)
    pts = equity_history.series(mid, "1W", now=sat + 3600)["points"]
    ests = [p for p in pts if p.get("est")]
    assert len(ests) == 2, "both club days before the Saturday anchor are filled"
    # Friday carries the anchor's own index -> equals the anchor value; Thursday
    # sits 10% below it, exactly the club's move.
    assert ests[-1]["v"] == 342.30
    assert abs(ests[0]["v"] - 342.30 / 1.10) < 0.01
