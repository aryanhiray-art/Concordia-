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
RANGE_DAYS: dict[str, int | None] = {
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


def _last_session_rows(member_id: int, at: int) -> list[tuple[int, float]]:
    """1D = the most recent ET TRADING DAY that has observations — not the
    trailing 24 hours.

    A trailing-24h window asked on a weekend holds only after-hours samples of
    an unmoving balance: a flat line ending "now", which the owner read (fairly)
    as a broken chart. Robinhood's own 1D does what this does — on Saturday it
    shows Friday's session. Implementation: walk back day by day (up to ~2
    weeks, covering holidays) until an ET calendar day that (a) was a trading
    day and (b) actually has points; serve that whole ET day. Falls back to the
    trailing 24h when no such day exists yet — recording may simply be younger
    than one session.
    """
    from datetime import datetime, timedelta

    from ..calendar_us import ET, is_trading_day
    day = datetime.fromtimestamp(at, ET).date()
    for _ in range(14):
        if is_trading_day(day):
            start = int(datetime(day.year, day.month, day.day, tzinfo=ET).timestamp())
            end = start + 86_400
            rows = [r for r in _rows(member_id, start) if r[0] < end]
            if rows:
                return rows
        day -= timedelta(days=1)
    return _rows(member_id, at - 86_400)


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
    if rng == "1D":
        rows = _last_session_rows(member_id, at)
    else:
        rows = _rows(member_id, None if days is None else at - days * 86_400)
    pts, resolution = _downsample(rows)
    return {
        "range": rng,
        "points": [{"t": ts, "v": v} for ts, v in pts],
        "resolution": resolution,
        "first_ts": first_ts(member_id),
        "ok": len(pts) >= 2,
    }
