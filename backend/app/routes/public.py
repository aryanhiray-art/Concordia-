"""The club's only unauthenticated surface: the public marketing site's API.

Every other route in this app is behind a session or an API key. These are
on the open internet, so they are written for the traffic that actually reaches
an open internet endpoint — scrapers, bots, and the occasional flood:

  * GET /stats NEVER recomputes. The decisions series costs a fan-out of
    Robinhood historicals calls made with a *member's* borrowed credentials
    (services/performance.club_performance), so letting an anonymous caller
    trigger it would hand strangers a lever on a real broker session and its
    rate limit. This route reads the CACHE ONLY, and answers with the published
    constants (stale=true) when the cache is cold — the marketing page always
    gets honest numbers, and never a request that blocks on the broker.
  * GET /tape serves market quotes for a FIXED editorial symbol list, cached,
    with one upstream Alpaca market-data call per TTL window no matter the
    traffic. The list is hardcoded and reads nothing from the club — see the
    held-symbols rule below.
  * POST /waitlist writes into the same single-writer SQLite file the executor
    uses to move real money. A public write path into that file is bounded three
    ways: a per-IP window, a global window, and a hard row cap.

What must never appear in either payload: member counts, member names, account
values, or the symbols the club currently holds. The roster of an invite-only
club is private; the published record is an aggregate index and nothing else.
"""
from __future__ import annotations

import json
import math
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import ratelimit
from ..config import settings
from ..db import audit, db, kv_get
from ..services import clubmirror, performance
from .deps import client_ip

router = APIRouter(prefix="/api/public", tags=["public"])


# ─── GET /stats ──────────────────────────────────────────────────────────────

# The published fallback (marketing SPEC §2) — the verified figures for the
# 2026-06-17..2026-08-24 window, independently recomputed over all 24 locked
# baskets. Serving these when the cache is cold keeps the page truthful without
# a recompute; `points` stays EMPTY because a fabricated curve would be a lie
# even when its endpoints are right. Callers render the numbers and skip the
# chart. `stale` is true exactly when these constants are what went out.
_FALLBACK: dict = {
    "live_since": "2026-06-17",
    # The window these constants actually cover. Without it the payload says
    # "+6.85% since 17 Jun" and nothing else, so a page rendering the fallback
    # implies the figure runs through TODAY — and the gap grows every week the
    # cache stays cold. On the live path this is the last marked close.
    "as_of": "2026-08-24",
    "n_sessions": 24,
    "club_pct": 6.85,
    "spy_pct": 3.04,
    "points": [],
    "stale": True,
}

# Answer repeat hits from memory rather than re-reading the kv row per request:
# the marketing page is the one thing here that can be linked on Hacker News,
# and a landing-page visit must not cost a SQLite open on the DB the executor
# needs to write. Same shape as clubmirror._cache.
_TTL_S = 60
_cache: dict = {"at": 0.0, "payload": None}


def _cached_series() -> dict | None:
    """The last GOOD club series, read only from caches — never recomputed.

    The demo instance's own kv cache is empty by construction (its DB has no
    real sessions), but it holds the real club's mirrored series on disk, which
    is the same non-identifying aggregate — prefer it there, exactly as
    routes/club does. Otherwise read services/performance's kv entry directly:
    going through club_performance() would recompute on a cold or expired cache,
    which is the one thing this endpoint must not do.
    """
    mirrored = clubmirror.performance()
    if isinstance(mirrored, dict) and mirrored.get("points"):
        return mirrored
    # performance._CACHE_KEY rather than a copy of the literal: the two must not
    # be able to drift apart, or this endpoint would quietly serve constants
    # forever while the real cache filled up under a different key.
    raw = kv_get(performance._CACHE_KEY)
    if not raw:
        return None
    try:
        cached = json.loads(raw)
    except ValueError:
        return None
    return cached if isinstance(cached, dict) else None


def _f(v) -> float | None:
    """A finite float, or None. NaN/inf reach json.dumps as bare NaN/Infinity —
    valid Python, invalid JSON, and a parse error in the visitor's browser."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _point(p) -> dict | None:
    """One {d, club, spy} row, or None when the cached row is unusable.

    `spy` is nullable by contract — a day the benchmark fetch could not cover
    leaves a gap in the benchmark line — but `club` is not: a day with no index
    value is not a point, and a 0 there would draw a crash that never happened.
    """
    if not isinstance(p, dict) or not p.get("d"):
        return None
    club = _f(p.get("club"))
    if club is None:
        return None
    return {"d": str(p["d"])[:10], "club": club, "spy": _f(p.get("spy"))}


def _fallback() -> dict:
    """A private copy of the constants — `points` must be a fresh list, since
    the built payload is memoised and handed out repeatedly."""
    return {**_FALLBACK, "points": []}


def _public_stats() -> dict:
    """Build the public payload from cache, or fall back to the constants."""
    series = _cached_series()
    raw_points = series.get("points") if isinstance(series, dict) else None
    if not isinstance(raw_points, list):
        raw_points = []
    points = [pt for pt in map(_point, raw_points) if pt is not None]
    if not points:
        out = _fallback()
    else:
        # Whitelist every field instead of forwarding the cached dict: the cache
        # is written by a service that is free to grow new keys, and this is the
        # one response nobody has to authenticate to read.
        out = {
            "live_since": series.get("start_date") or _FALLBACK["live_since"],
            "as_of": points[-1]["d"],
            "n_sessions": int(series.get("n_sessions") or 0),
            "club_pct": _f(series.get("club_pct")),
            "spy_pct": _f(series.get("spy_pct")),
            "points": points,
            "stale": False,
        }
        # A series with no computed percentages is a half-built cache entry, not
        # a track record — publish the verified constants instead of nulls.
        if out["club_pct"] is None or out["spy_pct"] is None:
            out = _fallback()
    # Names per basket, not the size of the S&P 500 pool they are chosen from —
    # this is the club's configured consensus width (SPEC §2, "~20 names per
    # cycle"), so it tracks TOP_N instead of being pinned in the fallback.
    out["universe_size"] = int(settings.top_n)
    return out


@router.get("/stats")
def public_stats(response: Response, request: Request):
    """The club's published record. No auth, cache-only, safe to hammer."""
    ip = client_ip(request)
    # Generous by design: one page load is one call, so this only ever stops a
    # flood. Nothing is recorded once the window trips, which keeps the limiter's
    # own per-key list bounded at the limit.
    if ratelimit.too_many(f"pubstats:{ip}", 120, 60):
        raise HTTPException(status_code=429, detail="too many requests")
    ratelimit.record(f"pubstats:{ip}")
    now = time.time()
    payload = _cache["payload"]
    if payload is None or _cache["at"] <= now - _TTL_S:
        payload = _public_stats()
        _cache["at"], _cache["payload"] = now, payload
    # Let the browser and any CDN in front of us absorb the repeats too — the
    # underlying series only moves once a day.
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"
    return payload


# ─── GET /tape ───────────────────────────────────────────────────────────────

# The marketing marquee's quote tape. A fixed editorial list — the largest S&P
# 500 components by market cap, written here by hand — NEVER a read of any club
# table. Whether any of these names is or is not in the club's basket, this
# list cannot say, because nothing about the club feeds it (the module
# docstring's held-symbols ban is the rule being kept). Quotes ride the
# reference account's Alpaca market-data key, which exists precisely so public
# numbers never touch a member's borrowed Robinhood session.
_TAPE_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
                 "AVGO", "TSLA", "JPM", "V", "UNH", "COST"]
_TAPE_TTL_S = 75             # at most one upstream call per window, any traffic
_TAPE_FAIL_COOLDOWN_S = 300  # after a failure, don't re-try the upstream for this long
_TAPE_MAX_STALE_S = 900      # past this age, an old quote is a lie — serve none
_tape_cache: dict = {"at": 0.0, "items": None, "failed_at": 0.0}


def _tape_items() -> list[dict]:
    """Quotes for the fixed list, or [] when the feed cannot answer honestly.

    [] is a real answer, not an error: the page renders its claims strip
    instead of the tape. Every failure mode lands there — keys not configured,
    upstream down, a symbol payload missing its bars — because the alternative
    in each case is inventing or half-inventing a number.
    """
    from ..alpaca.client import AlpacaClient, AlpacaError

    if not settings.alpaca_configured:
        return []
    try:
        snaps = AlpacaClient().snapshots(_TAPE_SYMBOLS)
    except AlpacaError:
        return []
    items: list[dict] = []
    for sym in _TAPE_SYMBOLS:
        s = snaps.get(sym) or {}
        # Latest trade when there is one (live or after-hours print), else the
        # day bar's close. The change is measured against the PREVIOUS day's
        # close — the number every tape on every finance site shows.
        price = (s.get("latestTrade") or {}).get("p")
        if price is None:
            price = (s.get("dailyBar") or {}).get("c")
        base = (s.get("prevDailyBar") or {}).get("c")
        try:
            price_f, base_f = float(price), float(base)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(price_f) and math.isfinite(base_f)) or price_f <= 0 or base_f <= 0:
            continue
        items.append({
            "sym": sym,
            "price": round(price_f, 2),
            "chg_pct": round((price_f / base_f - 1.0) * 100.0, 2),
        })
    return items


@router.get("/tape")
def public_tape(response: Response, request: Request):
    """Large-cap quotes for the marketing marquee. No auth, and the same
    stance as /stats: anonymous traffic can never fan out — one upstream
    market-data call per TTL window at most, a cooldown after a failure, and a
    hard staleness ceiling past which the tape goes empty rather than dressing
    old prices as fresh."""
    ip = client_ip(request)
    if ratelimit.too_many(f"pubtape:{ip}", 120, 60):
        raise HTTPException(status_code=429, detail="too many requests")
    ratelimit.record(f"pubtape:{ip}")
    now = time.time()
    fresh = _tape_cache["items"] is not None and _tape_cache["at"] > now - _TAPE_TTL_S
    cooling = _tape_cache["failed_at"] > now - _TAPE_FAIL_COOLDOWN_S
    if not fresh and not cooling:
        got = _tape_items()
        if got:
            _tape_cache["at"], _tape_cache["items"] = time.time(), got
            _tape_cache["failed_at"] = 0.0
        else:
            # Keep the last good quotes (the staleness ceiling below decides
            # how long they stay servable) and stop hitting the upstream.
            _tape_cache["failed_at"] = time.time()
    age = time.time() - _tape_cache["at"]
    items = _tape_cache["items"] if _tape_cache["items"] and age <= _TAPE_MAX_STALE_S else []
    response.headers["Cache-Control"] = "public, max-age=45, s-maxage=60"
    return {"items": items, "stale": bool(items) and age > _TAPE_TTL_S}


# ─── POST /waitlist ──────────────────────────────────────────────────────────

_MAX_EMAIL_LEN = 254            # RFC 5321 forward-path limit
_MAX_SOURCE_LEN = 32
# Backstop against a distributed spam run filling the disk under the money
# system. Reaching it means something has gone wrong, not that the club got
# popular — the per-IP and global windows cap honest growth far below this.
_MAX_ROWS = 25_000

# ASCII only, no IDN: every address here is destined to be pasted into an invite
# by hand, so bouncing a vanishingly rare unicode address is the cheaper mistake
# than storing something unmailable. Applied AFTER lowercasing + trimming.
_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}"
    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)
_SOURCE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class WaitlistBody(BaseModel):
    # Bounded at the model, not just in the handler: an oversized string should
    # never reach the normalisation code or the DB. (Total body size is nginx's
    # job — client_max_body_size — not FastAPI's.)
    email: str = Field(max_length=320)
    source: str | None = Field(default=None, max_length=64)


def _normalise_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if len(email) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(email):
        return None
    return email


def _normalise_source(raw: str | None) -> str | None:
    """Which block of the page submitted this, as a slug — never free text.

    It is client-supplied and lands in an owner-facing table, so anything that
    is not a plain slug is dropped rather than sanitised into something that
    still round-trips.
    """
    s = (raw or "").strip().lower()[:_MAX_SOURCE_LEN]
    return s if s and _SOURCE_RE.match(s) else None


@router.post("/waitlist")
def join_waitlist(body: WaitlistBody, request: Request):
    """Record an address from the public site. Always {"ok": true} once valid.

    A duplicate answers exactly like a first-time submit — same status, same
    body — because anything else turns this into an oracle for "is this person
    in the club?" on an endpoint with no auth in front of it. (The marketing
    SPEC sketched a 409 for duplicates; that would have leaked precisely the
    membership signal the same SPEC forbids, so this returns 200 for both.)

    This endpoint never sends mail. Nothing here is a signup: joining the club
    still requires an invite the owner issues by hand.
    """
    ip = client_ip(request)
    if ratelimit.too_many(f"waitlist:{ip}", 6, 3600):
        raise HTTPException(status_code=429, detail="too many requests — try again later")
    # A single global window as well: the per-IP cap is worth little against a
    # botnet, and this is a public INSERT into the executor's database.
    if ratelimit.too_many("waitlist:global", 300, 3600):
        raise HTTPException(status_code=429, detail="too many requests — try again later")
    # Metered BEFORE the validity check, not after: a rejected address must
    # still consume the window, or "submit garbage" is an unbounded, unmetered
    # path into this handler for as long as the flooder cares to hold it open.
    # Six attempts an hour is far above what mistyping an address costs a person.
    ratelimit.record(f"waitlist:{ip}")
    ratelimit.record("waitlist:global")
    email = _normalise_email(body.email)
    if email is None:
        # Plain 400 rather than pydantic's 422 list: this message is rendered
        # directly under a public input field.
        raise HTTPException(status_code=400, detail="enter a valid email address")
    now = int(time.time())
    with db() as conn:
        # MAX(id), not COUNT(*): an O(1) rowid lookup instead of a scan on every
        # public submit. It over-counts after deletions, which errs toward the
        # cap — the conservative direction for a backstop.
        row = conn.execute("SELECT MAX(id) AS n FROM waitlist").fetchone()
        if int((row["n"] if row else 0) or 0) >= _MAX_ROWS:
            # Audited at most hourly (the limiter doubles as the throttle) so the
            # cap is visible in the trail without the alert itself becoming the
            # write amplification it exists to prevent.
            if not ratelimit.too_many("waitlist:capped", 1, 3600):
                ratelimit.record("waitlist:capped")
                audit("public", "waitlist_capped", None, {"max_rows": _MAX_ROWS})
            return {"ok": True}
        # INSERT OR IGNORE is the dedupe: the UNIQUE(email) constraint absorbs a
        # repeat submit silently, so the response is identical either way.
        conn.execute(
            "INSERT OR IGNORE INTO waitlist(email,source,created_at) VALUES(?,?,?)",
            (email, _normalise_source(body.source), now),
        )
    return {"ok": True}
