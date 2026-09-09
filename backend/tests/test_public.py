"""Tests for the public (unauthenticated) marketing API — routes/public.py.

This is the club's only surface with no auth in front of it, so the contract
under test is as much about what it REFUSES to do as what it returns:

- GET /api/public/stats
  * 200 with no session, no cookie, no API key.
  * Exactly the SPEC §5 keys — no member count, no names, no account values, no
    currently-held symbols. Asserted as an exact key set AND as a substring
    sweep over the raw body, so a future field can't smuggle one in.
  * NEVER recomputes: with services/performance.club_performance monkeypatched
    to explode, the route still answers. An anonymous caller must not be able to
    trigger a fan-out of Robinhood calls made on a member's borrowed session.
  * Cold cache → the published §2 constants with stale=true and points=[]
    (a fabricated curve would be a lie even with the right endpoints).

- POST /api/public/waitlist
  * Accepts, lowercases and trims; stores one row.
  * A duplicate is answered IDENTICALLY (200 {"ok": true}) — a 409 would turn
    this into a "is this address in the club?" oracle.
  * Malformed input → 400, nothing written.
  * Per-IP window enforced.

Run from backend/:  python -m pytest tests/test_public.py -q
(Members/sessions inserted directly and per-test spoofed IPs, same rationale as
test_history_endpoints.py. The suite shares one process-wide DB and one
process-wide rate-limit dict, so every test here picks its own IP.)
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
# Mirror the env the alphabetically-first module (test_admin_panel) sets, so
# this module behaves the same alone as inside the full suite (one process-wide
# app.config singleton).
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, init_db, kv_set  # noqa: E402
from app.main import app  # noqa: E402
from app.routes import public  # noqa: E402
from app.services import performance  # noqa: E402

client = TestClient(app)   # no context manager → no startup event (init_db below)

_ip_counter = [0]

STATS_KEYS = {"live_since", "as_of", "n_sessions", "club_pct", "spy_pct", "points",
              "universe_size", "stale"}


def setup_module(_):
    init_db()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ip() -> str:
    """A fresh spoofed client IP (client_ip trusts the LAST XFF hop), so these
    tests never burn the shared per-IP windows of other test modules."""
    _ip_counter[0] += 1
    return f"10.91.0.{_ip_counter[0]}"


def _hdrs(ip: str | None = None) -> dict:
    return {"X-Forwarded-For": ip or _ip()}


def _seed_cache(points, n_sessions=24, start="2026-06-17",
                club_pct=6.85, spy_pct=3.04, **extra):
    """Write a services/performance-shaped payload into the kv cache."""
    kv_set(performance._CACHE_KEY, json.dumps({
        "start_date": start, "computed_at": int(time.time()),
        "n_sessions": n_sessions, "points": points,
        "club_pct": club_pct, "spy_pct": spy_pct,
        "ts": int(time.time()), "ok": True, **extra,
    }))


def _clear_cache():
    with db() as conn:
        conn.execute("DELETE FROM kv WHERE key=?", (performance._CACHE_KEY,))


@pytest.fixture(autouse=True)
def _fresh_stats_memo():
    """routes/public memoises the built stats payload for 60s and the quote
    tape for its own TTL; a test that seeds either cache must not be answered
    from the previous test's memo."""
    public._cache["at"], public._cache["payload"] = 0.0, None
    public._tape_cache.update(at=0.0, items=None, failed_at=0.0)
    # settings is a frozen dataclass (see conftest) — snapshot the alpaca keys
    # so a tape test that plants test credentials can't leak them onward.
    key0, sec0 = settings.alpaca_key_id, settings.alpaca_secret
    yield
    public._cache["at"], public._cache["payload"] = 0.0, None
    public._tape_cache.update(at=0.0, items=None, failed_at=0.0)
    object.__setattr__(settings, "alpaca_key_id", key0)
    object.__setattr__(settings, "alpaca_secret", sec0)


# ─── GET /stats ──────────────────────────────────────────────────────────────

def test_stats_needs_no_auth():
    _seed_cache([{"d": "2026-06-17", "club": 100.0, "spy": 100.0},
                 {"d": "2026-06-18", "club": 106.85, "spy": 103.04}])
    r = client.get("/api/public/stats", headers=_hdrs())
    assert r.status_code == 200
    body = r.json()
    assert set(body) == STATS_KEYS
    assert body["stale"] is False
    assert body["live_since"] == "2026-06-17"
    assert body["n_sessions"] == 24
    assert body["club_pct"] == 6.85 and body["spy_pct"] == 3.04
    assert body["universe_size"] == settings.top_n
    assert [p["d"] for p in body["points"]] == ["2026-06-17", "2026-06-18"]
    assert all(set(p) == {"d", "club", "spy"} for p in body["points"])
    # cache-only endpoint → tell the browser/CDN to absorb the repeats
    assert "max-age" in (r.headers.get("cache-control") or "")


def test_stats_ignores_a_presented_session_and_leaks_no_member_fields():
    """The payload is club-level and identical for everyone. Seed a member and a
    decided session, then sweep the raw body for anything member-shaped."""
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO members(email,display_name,role,created_at) VALUES(?,?,?,?)",
            ("public-leak-probe@example.com", "Leak Probe", "member", now))
        conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,"
            "closed_at,top_n,picks_json) VALUES(?,?,?,?,?,?,?)",
            ("2026-06-30", "executed", now, now, now, 3,
             json.dumps([{"symbol": "NVDA"}, {"symbol": "MSFT"}])))
    _seed_cache([{"d": "2026-06-17", "club": 100.0, "spy": 100.0}],
                # a stray key in the cache must not be forwarded
                members=7, symbols=["NVDA"])
    r = client.get("/api/public/stats", headers=_hdrs())
    assert r.status_code == 200
    assert set(r.json()) == STATS_KEYS
    raw = r.text
    for forbidden in ("member", "Leak Probe", "example.com", "NVDA", "MSFT",
                      "email", "account", "equity", "voting_power"):
        assert forbidden not in raw, f"public stats leaked {forbidden!r}"


def test_stats_never_recomputes_on_a_cold_cache():
    """The whole point of the endpoint. club_performance() would fan out to
    Robinhood with a member's borrowed credentials; an anonymous GET must never
    reach it — cold cache included."""
    _clear_cache()

    def _boom(*a, **k):
        raise AssertionError("public /stats must never trigger a recompute")

    original = performance.club_performance
    performance.club_performance = _boom          # type: ignore[assignment]
    try:
        r = client.get("/api/public/stats", headers=_hdrs())
    finally:
        performance.club_performance = original   # type: ignore[assignment]
    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is True
    assert body["points"] == []                   # never invented
    assert body["club_pct"] == 6.85 and body["spy_pct"] == 3.04
    assert body["n_sessions"] == 24
    assert body["live_since"] == "2026-06-17"
    assert body["universe_size"] == settings.top_n


def test_stats_falls_back_when_the_cached_series_is_empty():
    """services/performance's empty contract shape (a degraded recompute with no
    prior good series) is not a track record — publish the constants instead."""
    _seed_cache([], n_sessions=0, start=None, club_pct=None, spy_pct=None)
    body = client.get("/api/public/stats", headers=_hdrs()).json()
    assert body["stale"] is True and body["club_pct"] == 6.85


def test_stats_keeps_a_benchmark_gap_null_and_drops_an_unusable_point():
    """A missing SPY close is a gap in the benchmark line, not a zero — a 0 in a
    growth-of-100 series draws a crash that never happened. A point with no club
    value is not a point at all."""
    _seed_cache([
        {"d": "2026-06-17", "club": 100.0, "spy": 100.0},
        {"d": "2026-06-18", "club": 101.0, "spy": None},     # benchmark gap
        {"d": "2026-06-19", "club": None, "spy": 101.0},     # unusable
        {"d": "2026-06-20", "club": float("nan"), "spy": 1},  # NaN is not JSON
        "junk",
    ])
    r = client.get("/api/public/stats", headers=_hdrs())
    assert r.status_code == 200
    pts = r.json()["points"]
    assert [p["d"] for p in pts] == ["2026-06-17", "2026-06-18"]
    assert pts[1]["spy"] is None
    assert "NaN" not in r.text                # would be a browser parse error


def test_stats_is_rate_limited_per_ip():
    _seed_cache([{"d": "2026-06-17", "club": 100.0, "spy": 100.0}])
    ip = _ip()
    ratelimit.clear(f"pubstats:{ip}")
    codes = {client.get("/api/public/stats", headers=_hdrs(ip)).status_code
             for _ in range(121)}
    assert codes == {200, 429}
    ratelimit.clear(f"pubstats:{ip}")


# ─── POST /waitlist ──────────────────────────────────────────────────────────

def _join(email, ip=None, source=None):
    payload: dict = {"email": email}
    if source is not None:
        payload["source"] = source
    return client.post("/api/public/waitlist", json=payload, headers=_hdrs(ip))


def _rows(email: str) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM waitlist WHERE email=?", (email,)).fetchall()]


def test_waitlist_accepts_and_normalises():
    r = _join("  Rohan.Test+Club@Example.COM  ", source="hero")
    assert r.status_code == 200 and r.json() == {"ok": True}
    rows = _rows("rohan.test+club@example.com")
    assert len(rows) == 1
    assert rows[0]["source"] == "hero"
    assert rows[0]["created_at"] > 0


def test_waitlist_dedupes_silently():
    """Second submit is byte-identical to the first: no 409, no 'already' — the
    response must not reveal that the address is known."""
    email = "dupe@example.com"
    first = _join(email)
    second = _join(email.upper())        # same address, different casing
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"ok": True}
    assert len(_rows(email)) == 1


def test_waitlist_rejects_malformed_input():
    for bad in ("", "   ", "not-an-email", "a@b", "@example.com", "a@@b.com",
                "two words@example.com", "a@example.c", "x" * 250 + "@example.com"):
        r = _join(bad)
        assert r.status_code == 400, f"{bad!r} was accepted"
    # over the model's own ceiling → rejected before normalisation runs
    assert _join("x" * 400 + "@example.com").status_code == 422
    with db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM waitlist WHERE email LIKE '%not-an-email%'"
        ).fetchone()["n"] == 0


def test_waitlist_drops_a_non_slug_source_rather_than_storing_it():
    r = _join("sourcey@example.com", source="<script>alert(1)</script>")
    assert r.status_code == 200
    assert _rows("sourcey@example.com")[0]["source"] is None


def test_waitlist_meters_malformed_submits_too():
    """A rejected address still spends the window. Otherwise "submit garbage" is
    an unmetered path into the handler that a flooder can hold open forever."""
    ip = _ip()
    ratelimit.clear(f"waitlist:{ip}")
    for _ in range(6):
        assert _join("not-an-email", ip=ip).status_code == 400
    assert _join("late@example.com", ip=ip).status_code == 429
    assert _rows("late@example.com") == []
    ratelimit.clear(f"waitlist:{ip}")


def test_waitlist_is_rate_limited_per_ip():
    ip = _ip()
    ratelimit.clear(f"waitlist:{ip}")
    codes = [_join(f"flood{i}@example.com", ip=ip).status_code for i in range(8)]
    assert codes[:6] == [200] * 6
    assert codes[6:] == [429, 429]
    # a different visitor is unaffected by the flooder's window
    assert _join("innocent@example.com").status_code == 200
    ratelimit.clear(f"waitlist:{ip}")


# ─── GET /tape ───────────────────────────────────────────────────────────────
# The quote tape's contract mirrors /stats: no auth, no fan-out (one upstream
# call per TTL no matter the traffic), and honesty over availability — every
# failure mode answers [] (the page falls back to its claims strip) rather
# than a defaulted or stale-dressed-as-fresh price.

from app.alpaca.client import AlpacaTransientError  # noqa: E402


def _configure_alpaca(_monkeypatch=None):
    # frozen dataclass — the established override is object.__setattr__; the
    # autouse fixture restores the real values after every test.
    object.__setattr__(settings, "alpaca_key_id", "test-key")
    object.__setattr__(settings, "alpaca_secret", "test-secret")


def _snap(price: float, prev_close: float) -> dict:
    return {"latestTrade": {"p": price},
            "dailyBar": {"c": price},
            "prevDailyBar": {"c": prev_close}}


def test_tape_serves_quotes_for_exactly_the_fixed_editorial_list(monkeypatch):
    _configure_alpaca(monkeypatch)
    fake = {sym: _snap(102.0, 100.0) for sym in public._TAPE_SYMBOLS}
    # Upstream also answering for a symbol we did not ask about must not widen
    # the tape — the response is driven by OUR list, never the feed's.
    fake["GME"] = _snap(999.0, 1.0)
    monkeypatch.setattr("app.alpaca.client.AlpacaClient.snapshots",
                        lambda self, syms: fake)
    r = client.get("/api/public/tape", headers=_hdrs())
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "stale"}
    assert body["stale"] is False
    assert [i["sym"] for i in body["items"]] == public._TAPE_SYMBOLS
    one = body["items"][0]
    assert set(one) == {"sym", "price", "chg_pct"}
    assert one["price"] == 102.0 and one["chg_pct"] == 2.0


def test_tape_answers_empty_on_feed_failure_and_stops_retrying(monkeypatch):
    _configure_alpaca(monkeypatch)
    calls = [0]

    def boom(self, syms):
        calls[0] += 1
        raise AlpacaTransientError("data API down")

    monkeypatch.setattr("app.alpaca.client.AlpacaClient.snapshots", boom)
    r1 = client.get("/api/public/tape", headers=_hdrs())
    r2 = client.get("/api/public/tape", headers=_hdrs())
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["items"] == [] and r2.json()["items"] == []
    # The cooldown is the anti-fan-out guarantee: anonymous traffic hammering
    # the route while Alpaca is down must not become a hammer ON Alpaca.
    assert calls[0] == 1


def test_tape_without_alpaca_keys_is_empty_and_never_builds_a_client(monkeypatch):
    object.__setattr__(settings, "alpaca_key_id", "")
    object.__setattr__(settings, "alpaca_secret", "")

    def must_not_run(self, syms):  # pragma: no cover - the assert IS the test
        raise AssertionError("snapshots called with no keys configured")

    monkeypatch.setattr("app.alpaca.client.AlpacaClient.snapshots", must_not_run)
    r = client.get("/api/public/tape", headers=_hdrs())
    assert r.status_code == 200 and r.json()["items"] == []


def test_tape_drops_a_symbol_it_cannot_price_instead_of_defaulting(monkeypatch):
    _configure_alpaca(monkeypatch)
    fake = {sym: _snap(102.0, 100.0) for sym in public._TAPE_SYMBOLS}
    fake["NVDA"] = {"latestTrade": {"p": None}, "dailyBar": {}, "prevDailyBar": {}}
    monkeypatch.setattr("app.alpaca.client.AlpacaClient.snapshots",
                        lambda self, syms: fake)
    body = client.get("/api/public/tape", headers=_hdrs()).json()
    syms = [i["sym"] for i in body["items"]]
    assert "NVDA" not in syms
    assert len(syms) == len(public._TAPE_SYMBOLS) - 1


def test_tape_serves_a_briefly_stale_answer_but_labels_it(monkeypatch):
    public._tape_cache["items"] = [{"sym": "AAPL", "price": 1.0, "chg_pct": 0.0}]
    public._tape_cache["at"] = time.time() - (public._TAPE_TTL_S + 30)
    public._tape_cache["failed_at"] = time.time()      # cooling → no refresh
    body = client.get("/api/public/tape", headers=_hdrs()).json()
    assert body["items"] and body["stale"] is True


def test_tape_refuses_to_dress_old_quotes_as_fresh(monkeypatch):
    public._tape_cache["items"] = [{"sym": "AAPL", "price": 1.0, "chg_pct": 0.0}]
    public._tape_cache["at"] = time.time() - (public._TAPE_MAX_STALE_S + 5)
    public._tape_cache["failed_at"] = time.time()      # cooling → no refresh
    body = client.get("/api/public/tape", headers=_hdrs()).json()
    assert body["items"] == []


def test_tape_is_rate_limited_per_ip(monkeypatch):
    _configure_alpaca(monkeypatch)
    monkeypatch.setattr("app.alpaca.client.AlpacaClient.snapshots",
                        lambda self, syms: {s: _snap(10.0, 10.0)
                                            for s in public._TAPE_SYMBOLS})
    ip = _ip()
    codes = [client.get("/api/public/tape", headers=_hdrs(ip)).status_code
             for _ in range(121)]
    assert codes[0] == 200 and codes[-1] == 429
