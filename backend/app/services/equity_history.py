"""Recorded history of a member's total account value — the Overview chart.

WHY THIS EXISTS
    The member's account line used to be drawn from voting_power_history.equity,
    which is written ONCE A WEEK (services/power.snapshot_all, Sundays ET, capped
    ~26 rows). The frontend took those ~6 weekly anchors and linearly
    interpolated them into ~105 chart points, so almost every pixel of that curve
    was invented: it had no daily detail because no daily detail was ever stored.

    It cannot be reconstructed either. Robinhood's tool surface has get_portfolio
    (a CURRENT snapshot) plus equity/option/index historicals — there is no
    portfolio-value history endpoint. So account history has to be RECORDED going
    forward. That is all this module does.

HONESTY RULES (the whole point of the module)
    - Every stored row is a real observation of the broker's `total_value`.
    - A failed / missing balance read stores NOTHING. A gap is honest; a false $0
      on a money chart is not.
    - Downsampling for a long range picks the LAST REAL point in each bucket. It
      never averages, never interpolates, never synthesises a point.
    - A range with fewer than 2 points comes back `ok: false` so the UI says
      "not enough history yet" instead of drawing a line between two dots.

WHERE THE WRITE LIVES
    services/accountsnap.refresh(), on its SUCCESS path only (see record_from_
    payload). That function already reads the broker's portfolio every ~10 min
    per member for the display cache, so a point costs zero extra broker calls,
    and its failure branch returns before ever reaching the write — a broker
    outage can only produce a gap, never a bogus row.
"""
from __future__ import annotations

import math
import time

from ..db import db, kv_get, kv_set

# ─── retention ───────────────────────────────────────────────────────────────
# POLICY: keep EVERY observation for the last FINE_WINDOW_DAYS days (that is the
# ~10-minute intraday detail the 1D/1W views live on), and beyond that window
# keep only the LAST point of each day bucket — one real daily close-ish value.
# Nothing older is discarded, so ALL keeps its full shape at daily resolution
# (~365 rows/member/year) and the table cannot grow without bound.
#
# Buckets are UTC days. A UTC day ends at 19:59/20:59 ET, i.e. AFTER the US
# close, so the surviving point of each day is that day's settled account value.
FINE_WINDOW_DAYS = 7
DAY_BUCKET_SECS = 86_400

# Sanity bound on a single observation: anything outside [0, MAX_SANE_VALUE] is
# treated as a bad read and dropped rather than plotted.
MAX_SANE_VALUE = 1e12

# ─── ranges + downsampling ───────────────────────────────────────────────────
# Windows mirror services/stocks._RANGES so the account chart and the stock
# charts mean the same thing by "3M". ALL has no cutoff.
# 1D was removed (owner call, 2026-08-02): recording is younger than one
# trading session, so a 1D view could only show an after-hours flatline or a
# club-shaped estimate of a day we never observed — both worse than no tab.
# The 10-minute recorder keeps running precisely so 1D can come back once a
# few real sessions exist.
RANGE_DAYS: dict[str, int | None] = {
    # 1D exists again because the recorder has been keeping FINE_WINDOW_DAYS of
    # raw 10-minute observations since 2026-08 — a 1-day window now slices real
    # intraday points instead of fanning out invented ones.
    "1D": 1, "1W": 7, "1M": 31, "3M": 92, "1Y": 366, "ALL": None,
}
DEFAULT_RANGE = "1M"

# Never ship more than this many points for one range.
MAX_POINTS = 400
# Coarsening ladder: the first bucket size that gets the count under MAX_POINTS
# wins. `None` = raw (every recorded observation, no bucketing at all).
_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (None, "raw"),
    (900, "15m"),
    (1800, "30m"),
    (3600, "1h"),
    (4 * 3600, "4h"),
    (86_400, "1d"),
    (7 * 86_400, "1w"),
)

_SEED_FLAG = "equity_points_seeded_at"


class UnknownRange(LookupError):
    """Range label the API does not serve."""


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ─── write ───────────────────────────────────────────────────────────────────

def record(member_id: int, total_value, ts: int | None = None) -> bool:
    """Store ONE observed account value. Returns True if a row was written.

    Refuses (returns False, writes nothing) when the value is None, non-numeric,
    NaN/inf, negative, or absurd — i.e. exactly the shapes a failed or partial
    broker read produces. $0.00 IS accepted: an empty account really is zero,
    and a failed read never reports 0, it reports None.
    """
    v = _num(total_value)
    if v is None or v < 0 or v > MAX_SANE_VALUE:
        return False
    at = int(ts if ts is not None else time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO equity_points(member_id,ts,total_value) VALUES(?,?,?) "
            "ON CONFLICT(member_id,ts) DO UPDATE SET total_value=excluded.total_value",
            (int(member_id), at, v),
        )
    return True


def record_from_payload(member_id: int, payload: dict, ts: int | None = None) -> bool:
    """Record the point carried by a SUCCESSFUL accountsnap payload.

    `payload["allocation"]["balance"]` is None whenever the balance read failed
    or the member has no connected Robinhood account, which is the gate: no
    balance, no point. Never raises — a chart-history bug must not be able to
    break the display cache that calls this.
    """
    try:
        bal = ((payload or {}).get("allocation") or {}).get("balance")
        if not isinstance(bal, dict):
            return False
        return record(member_id, bal.get("total_value"), ts)
    except Exception:  # noqa: BLE001 — history is best-effort, the caller is not
        return False


# ─── retention ───────────────────────────────────────────────────────────────

def prune(now: int | None = None) -> int:
    """Apply the retention policy across all members. Returns rows deleted.

    Everything newer than the fine window is untouched. Older than it, a row
    survives only if it is the newest pre-cutoff row of its UTC day for that
    member. The inner MAX is itself bounded by the cutoff so a day straddling
    the boundary still keeps its last point on the old side.
    """
    cutoff = int(now if now is not None else time.time()) - FINE_WINDOW_DAYS * 86_400
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM equity_points WHERE ts < ? AND ts <> ("
            "  SELECT MAX(x.ts) FROM equity_points x"
            "   WHERE x.member_id = equity_points.member_id"
            "     AND CAST(x.ts / ? AS INTEGER) = CAST(equity_points.ts / ? AS INTEGER)"
            "     AND x.ts < ?)",
            (cutoff, DAY_BUCKET_SECS, DAY_BUCKET_SECS, cutoff),
        )
        return int(cur.rowcount or 0)


# ─── one-time seed from the weekly history ───────────────────────────────────

def seed_from_voting_power(force: bool = False) -> dict:
    """Backfill the weekly voting_power_history.equity rows as equity points.

    Those rows are REAL observations — merely sparse (one per Sunday) — so a
    member's chart is not empty on day one. Nothing here is fabricated and
    nothing is marked as anything other than what it is: an observation taken at
    `created_at`.

    Idempotent twice over: INSERT OR IGNORE against the UNIQUE(member_id, ts)
    index, plus a kv flag so the seed runs ONCE ever. The flag matters because
    prune() legitimately thins these rows later — without it, a re-seed would
    resurrect points retention had already decided to drop.
    """
    if not force and kv_get(_SEED_FLAG):
        return {"seeded": False, "inserted": 0, "reason": "already_seeded"}
    with db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO equity_points(member_id,ts,total_value) "
            "SELECT member_id, created_at, equity FROM voting_power_history "
            " WHERE equity IS NOT NULL AND equity >= 0 AND equity <= ? "
            "   AND created_at IS NOT NULL AND created_at > 0",
            (MAX_SANE_VALUE,),
        )
        inserted = int(cur.rowcount or 0)
    kv_set(_SEED_FLAG, str(int(time.time())))
    return {"seeded": True, "inserted": inserted}


# ─── read ────────────────────────────────────────────────────────────────────

def first_ts(member_id: int) -> int | None:
    """Epoch of the member's EARLIEST recorded point, across all history — what
    lets the UI say "recording since ..." instead of implying the chart's left
    edge is the start of the account."""
    with db() as conn:
        row = conn.execute("SELECT MIN(ts) AS t FROM equity_points WHERE member_id=?",
                           (int(member_id),)).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def _rows(member_id: int, since: int | None) -> list[tuple[int, float]]:
    sql = "SELECT ts, total_value FROM equity_points WHERE member_id=?"
    args: list = [int(member_id)]
    if since is not None:
        sql += " AND ts >= ?"
        args.append(int(since))
    with db() as conn:
        return [(int(r["ts"]), float(r["total_value"]))
                for r in conn.execute(sql + " ORDER BY ts ASC", args).fetchall()]


def _downsample(rows: list[tuple[int, float]]) -> tuple[list[tuple[int, float]], str]:
    """Thin `rows` to <= MAX_POINTS by keeping the LAST REAL observation in each
    time bucket. Returns (rows, resolution-label). No averaging: every returned
    pair is a value the broker actually reported at that exact second."""
    for size, label in _BUCKETS:
        if size is None:
            if len(rows) <= MAX_POINTS:
                return rows, label
            continue
        kept: dict[int, tuple[int, float]] = {}
        for ts, v in rows:                       # ascending → last write wins
            kept[ts // size] = (ts, v)
        if len(kept) <= MAX_POINTS:
            return [kept[k] for k in sorted(kept)], label
    # Longer than the ladder's coarsest bucket: take the newest MAX_POINTS of it.
    size, label = _BUCKETS[-1]
    kept = {}
    for ts, v in rows:
        kept[ts // int(size)] = (ts, v)
    return [kept[k] for k in sorted(kept)[-MAX_POINTS:]], label


# ─── club-shaped densification ───────────────────────────────────────────────
# The recorded history is sparse (weekly snapshots until 2026-08-01, dense
# 10-minute observations after). A straight segment across a two-week gap reads
# as "nothing happened", which is false — the member's money was IN the club's
# basket the whole time, moving with it. Owner call: fill the gaps at DAILY
# grain by shaping them along the club's own real daily performance, anchored
# to the real observations at both ends. One real $110 after a +10% club run
# draws 100→110 along the club's actual path, not a cliff.
#
# Estimated points carry est:true. Days that have a real observation always use
# the real value; the estimate never overrides a recording, and as the 10-min
# recorder accumulates real sessions the estimates retire day by day.

# Estimates are stamped at the ET close of their day.
_EST_HOUR_ET = 16


def _club_shape() -> dict[str, float]:
    """date "YYYY-MM-DD" → club index (base 100), from the club's real record.

    On the demo instance the club record is the MIRRORED real club (the demo's
    own DB has no decided cycles), so demo members' gaps are shaped by the same
    curve live members see. Empty dict = no shape available → no densification.
    """
    try:
        from . import clubmirror
        mirrored = clubmirror.performance()
        perf = mirrored if mirrored is not None else None
    except Exception:  # noqa: BLE001
        perf = None
    if perf is None:
        try:
            from . import performance as _perf
            perf = _perf.club_performance()
        except Exception:  # noqa: BLE001
            return {}
    out: dict[str, float] = {}
    for pt in (perf or {}).get("points") or []:
        d, c = pt.get("d"), pt.get("club")
        if isinstance(d, str) and isinstance(c, (int, float)) and c > 0:
            out[d] = float(c)
    return out


def _et_day_close(day: str) -> int:
    from datetime import datetime

    from ..calendar_us import ET
    y, m, d = (int(x) for x in day.split("-"))
    return int(datetime(y, m, d, _EST_HOUR_ET, tzinfo=ET).timestamp())


def _densify(rows: list[tuple[int, float]], at: int) -> list[dict]:
    """Real observations + club-shaped daily estimates for the gaps between
    (and before) them. Returns [{t, v[, est]}] ascending; real rows verbatim.

    Between anchors (t0,v0)→(t1,v1): v_day = v0 · (club_day/club_d0) · blendᵏ,
    where blend is the per-step residual that makes the path land EXACTLY on v1
    — the club shape provides the curve, the anchors stay the truth. Before the
    first anchor: pure back-extrapolation v_first · club_day/club_dfirst (the
    owner's 100→110 example). Nothing is extrapolated past the last real
    observation — the future is recorded, not invented.
    """
    from datetime import datetime

    from ..calendar_us import ET
    if not rows:
        return []
    shape = _club_shape()
    real = [{"t": ts, "v": v} for ts, v in rows]
    if not shape:
        return real

    days = sorted(shape)
    day_of = lambda ts: datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")  # noqa: E731
    have = {day_of(ts) for ts, _ in rows}
    out: list[dict] = list(real)

    def idx_at(day: str) -> float | None:
        """Club index effective ON a day: the latest shape day at or before it.
        Anchors routinely land where the shape has no exact entry — the first
        recording happened on a SATURDAY while the club series ends Friday —
        and requiring an exact hit silently disabled all backfill for them."""
        import bisect
        i = bisect.bisect_right(days, day)
        return shape[days[i - 1]] if i else None

    # Before the first observation: club shape scaled to END at the first value.
    t0, v0 = rows[0]
    d0 = day_of(t0)
    idx0 = idx_at(d0)
    if idx0 and v0 > 0:
        for d in days:
            if d >= d0 or d in have:
                continue
            out.append({"t": _et_day_close(d), "v": round(v0 * shape[d] / idx0, 2),
                        "est": True})

    # Between consecutive observations: club shape, residual-blended to land on
    # the right-hand anchor.
    for (ta, va), (tb, vb) in zip(rows, rows[1:]):
        da, db = day_of(ta), day_of(tb)
        ia, ib = idx_at(da), idx_at(db)
        if not ia or not ib or va <= 0:
            continue
        between = [d for d in days if da < d < db and d not in have]
        if not between:
            continue
        club_end = ib / ia
        target = vb / va
        # per-step multiplicative residual so the last estimate meets vb
        n = len(between) + 1
        resid = (target / club_end) ** (1.0 / n) if club_end > 0 else 1.0
        for k, d in enumerate(between, start=1):
            v = va * (shape[d] / ia) * (resid ** k)
            out.append({"t": _et_day_close(d), "v": round(v, 2), "est": True})

    out.sort(key=lambda p: p["t"])
    return out


def series(member_id: int, rng: str = DEFAULT_RANGE, now: int | None = None) -> dict:
    """{range, points:[{t,v}], resolution, first_ts, ok} for one range.

    `points` are recorded observations inside the range's window, oldest first.
    `resolution` names the bucket they were thinned to ("raw" = untouched).
    `first_ts` is the member's earliest point across ALL history, so the UI can
    date the start of recording even when the selected range is empty.
    `ok` is False when there are fewer than 2 points — the UI must then say
    "not enough history yet" rather than drawing a line.
    """
    rng = (rng or "").strip().upper()
    if rng not in RANGE_DAYS:
        raise UnknownRange("no such range")
    days = RANGE_DAYS[rng]
    at = int(now if now is not None else time.time())
    # Densify over the member's FULL history first (anchors outside the window
    # still shape what is inside it), then slice to the window.
    all_rows = _rows(member_id, None)
    dense = _densify(all_rows, at)
    cutoff = None if days is None else at - days * 86_400
    windowed = [p for p in dense if cutoff is None or p["t"] >= cutoff]
    # Downsample REAL points only when the window is oversized; estimates are
    # daily already and never dominate the payload.
    if len(windowed) > MAX_POINTS:
        real = [(p["t"], p["v"]) for p in windowed if not p.get("est")]
        ests = [p for p in windowed if p.get("est")]
        pts, resolution = _downsample(real)
        windowed = sorted(
            [{"t": ts, "v": v} for ts, v in pts] + ests, key=lambda p: p["t"])
    else:
        resolution = "raw"
    return {
        "range": rng,
        "points": windowed,
        "resolution": resolution,
        "first_ts": first_ts(member_id),
        "ok": len(windowed) >= 2,
    }
