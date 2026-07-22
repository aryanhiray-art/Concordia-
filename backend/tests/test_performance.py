"""Tests for the club performance series (services/performance.py).

build_series is pure (no network, no DB) — the model is pinned here:
  * equal-weight decisions index, base 100 at the first benchmark date
  * a decision takes effect the day AFTER its trade_date (basket chosen by
    trade_date <= previous timeline date)
  * a symbol missing either close drops out of that day's mean (renormalized)
  * no cycles / no benchmark bars → the empty contract shape

decided_cycles is exercised against the shared temp DB (same env-before-import
pattern as test_executor_fixes.py). Other test files write sessions/executions
to the same per-process DB, so these assert membership + relative order, never
the full list.

Run from backend/:  python -m pytest tests/test_performance.py -q
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

from app.db import db, init_db  # noqa: E402
from app.services import performance  # noqa: E402


def setup_module(_):
    init_db()


EMPTY = {"start_date": None, "computed_at": 0, "n_sessions": 0,
         "points": [], "club_pct": None, "spy_pct": None}


def _bars(**per_symbol):
    """_bars(SPY=[("2026-01-05", 100.0), ...], AAPL=[...]) → the bars dict."""
    return {sym: dict(series) for sym, series in per_symbol.items()}


# ─── build_series (pure) ─────────────────────────────────────────────────────

def test_flat_prices_hold_both_indexes_at_100():
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAPL"]}]
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    bars = _bars(SPY=[(d, 100.0) for d in dates], AAPL=[(d, 50.0) for d in dates])
    out = performance.build_series(cycles, bars)
    assert out["start_date"] == "2026-01-05"
    assert out["n_sessions"] == 1
    assert [p["d"] for p in out["points"]] == dates
    assert all(p["club"] == 100.0 and p["spy"] == 100.0 for p in out["points"])
    assert out["club_pct"] == 0.0 and out["spy_pct"] == 0.0


def test_single_symbol_doubling_gives_100_pct():
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAPL"]}]
    bars = _bars(
        SPY=[("2026-01-05", 400.0), ("2026-01-06", 404.0), ("2026-01-07", 440.0)],
        AAPL=[("2026-01-05", 100.0), ("2026-01-06", 150.0), ("2026-01-07", 200.0)],
    )
    out = performance.build_series(cycles, bars)
    assert out["points"][-1]["club"] == 200.0
    assert out["club_pct"] == 100.0
    assert out["spy_pct"] == 10.0                       # 440/400
    assert out["points"][1]["spy"] == 101.0             # 404/400


def test_benchmark_dates_before_first_cycle_are_dropped():
    cycles = [{"trade_date": "2026-01-06", "symbols": ["AAPL"]}]
    bars = _bars(
        SPY=[("2026-01-02", 90.0), ("2026-01-06", 100.0), ("2026-01-07", 110.0)],
        AAPL=[("2026-01-06", 10.0), ("2026-01-07", 10.0)],
    )
    out = performance.build_series(cycles, bars)
    assert [p["d"] for p in out["points"]] == ["2026-01-06", "2026-01-07"]
    assert out["start_date"] == "2026-01-06"
    assert out["spy_pct"] == 10.0                       # rebased at 01-06, not 01-02


def test_basket_switch_takes_effect_day_after_trade_date():
    """Cycle 2 lands on 01-07: the 01-06→01-07 step still uses cycle 1's basket
    (chosen by trade_date <= prev); B's move only counts on 01-07→01-08."""
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAA"]},
              {"trade_date": "2026-01-07", "symbols": ["BBB"]}]
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    bars = _bars(
        SPY=[(d, 100.0) for d in dates],
        AAA=[(d, 10.0) for d in dates],                              # flat throughout
        BBB=[("2026-01-05", 10.0), ("2026-01-06", 20.0),             # pre-switch moves
             ("2026-01-07", 10.0), ("2026-01-08", 20.0)],            # +100% post-switch
    )
    out = performance.build_series(cycles, bars)
    assert [p["club"] for p in out["points"]] == [100.0, 100.0, 100.0, 200.0]
    assert out["club_pct"] == 100.0


def test_symbol_missing_a_close_drops_from_that_days_mean():
    """AAA +10%, BBB unpriced on day 2 → the day's mean = AAA alone (+10%),
    not (10% + 0)/2."""
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAA", "BBB"]}]
    bars = _bars(
        SPY=[("2026-01-05", 100.0), ("2026-01-06", 100.0)],
        AAA=[("2026-01-05", 100.0), ("2026-01-06", 110.0)],
        BBB=[("2026-01-05", 50.0)],                                  # no 01-06 close
    )
    out = performance.build_series(cycles, bars)
    assert out["points"][-1]["club"] == 110.0
    assert out["club_pct"] == 10.0


def test_no_priced_basket_symbol_is_a_flat_day():
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAA"]}]
    bars = _bars(SPY=[("2026-01-05", 100.0), ("2026-01-06", 105.0)])  # no AAA bars at all
    out = performance.build_series(cycles, bars)
    assert [p["club"] for p in out["points"]] == [100.0, 100.0]
    assert out["club_pct"] == 0.0 and out["spy_pct"] == 5.0


def test_empty_cycles_returns_empty_shape():
    assert performance.build_series([], _bars(SPY=[("2026-01-05", 100.0)])) == EMPTY


def test_missing_benchmark_returns_empty_shape():
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAPL"]}]
    assert performance.build_series(cycles, _bars(AAPL=[("2026-01-05", 100.0)])) == EMPTY
    # benchmark present but only BEFORE the first decision → still no timeline
    assert performance.build_series(cycles, _bars(SPY=[("2026-01-02", 100.0)])) == EMPTY


def test_indexes_round_to_4dp_and_pcts_to_2dp():
    cycles = [{"trade_date": "2026-01-05", "symbols": ["AAA"]}]
    bars = _bars(
        SPY=[("2026-01-05", 3.0), ("2026-01-06", 4.0)],              # 4/3 → repeating decimal
        AAA=[("2026-01-05", 3.0), ("2026-01-06", 4.0)],
    )
    out = performance.build_series(cycles, bars)
    assert out["points"][-1]["club"] == 133.3333
    assert out["points"][-1]["spy"] == 133.3333
    assert out["club_pct"] == 33.33 and out["spy_pct"] == 33.33


# ─── decided_cycles (temp DB) ────────────────────────────────────────────────

def _session(trade_date: str, picks: list[dict] | None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,top_n,picks_json) VALUES(?,?,?,?)",
            (trade_date, "executed", 3, json.dumps(picks) if picks is not None else None),
        )
        return cur.lastrowid


def _placed(session_id: int, symbol: str, status: str = "placed") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,status,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (session_id, 1, symbol, "buy", status, int(time.time())),
        )


def test_decided_cycles_selects_decided_sessions_in_date_order():
    picks_a = [{"symbol": "AAPL", "votes": 3}, {"symbol": "MSFT", "votes": 2}]
    picks_b = [{"symbol": "NVDA", "votes": 1}]
    # inserted out of date order to prove the ORDER BY
    sid_b = _session("2027-02-02", picks_b)
    sid_a = _session("2027-02-01", picks_a)
    _placed(sid_a, "AAPL")
    _placed(sid_a, "MSFT")               # 2 placed rows, still ONE cycle
    _placed(sid_b, "NVDA")
    cycles = performance.decided_cycles()
    a = next(c for c in cycles if c["trade_date"] == "2027-02-01")
    b = next(c for c in cycles if c["trade_date"] == "2027-02-02")
    assert a["symbols"] == ["AAPL", "MSFT"]
    assert b["symbols"] == ["NVDA"]
    assert cycles.index(a) < cycles.index(b)
    assert [c["trade_date"] for c in cycles] == sorted(c["trade_date"] for c in cycles)


def test_decided_cycles_counts_decisions_not_fills():
    """A closed/executed session with locked picks IS a decision even when zero
    orders placed (all names at target, e.g. 2026-07-01) or a place errored."""
    _session("2027-02-03", [{"symbol": "TSLA", "votes": 1}])               # no executions
    sid_error = _session("2027-02-04", [{"symbol": "TSLA", "votes": 1}])
    _placed(sid_error, "TSLA", status="error")
    dates = {c["trade_date"] for c in performance.decided_cycles()}
    assert {"2027-02-03", "2027-02-04"} <= dates


def test_decided_cycles_skips_open_and_pickless_sessions():
    sid_nopicks = _session("2027-02-05", [])
    _placed(sid_nopicks, "TSLA")                                           # placed but no picks
    sid_nullpicks = _session("2027-02-06", None)
    _placed(sid_nullpicks, "TSLA")
    with db() as conn:                                                     # open = not yet decided
        conn.execute("INSERT INTO vote_sessions(trade_date,status,top_n,picks_json) VALUES(?,?,?,?)",
                     ("2027-02-07", "open", 3, json.dumps([{"symbol": "TSLA"}])))
    dates = {c["trade_date"] for c in performance.decided_cycles()}
    assert {"2027-02-05", "2027-02-06", "2027-02-07"}.isdisjoint(dates)


# ─── club_performance cache/backoff orchestration ───────────────────────────
# A partial or failed fetch must NEVER cache/serve a silently-degraded series:
# it serves the last GOOD series (or the empty shape) and stamps an ok=False
# entry so the broken feed is retried at most every _RETRY_TTL_S.

class _FailingClient:
    """Historicals client whose batches fail on demand (by symbol membership)."""

    def __init__(self, bars_by_symbol, fail_if=lambda batch: False):
        self.bars = bars_by_symbol
        self.fail_if = fail_if
        self.calls = 0

    def get_equity_historicals(self, symbols, start_time, interval="day"):
        self.calls += 1
        if self.fail_if(symbols):
            raise RuntimeError("simulated MCP outage")
        return {"data": {"results": [
            {"symbol": s, "bars": [{"begins_at": f"{d}T00:00:00Z", "close_price": c}
                                   for d, c in self.bars.get(s, {}).items()]}
            for s in symbols]}}


def _seed_decided_session(trade_date, symbols):
    picks = json.dumps([{"symbol": s} for s in symbols])
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO vote_sessions(trade_date,status,opened_at,closes_at,top_n,picks_json) "
            "VALUES(?,?,?,?,?,?)", (trade_date, "executed", 0, 0, 20, picks))
        conn.execute(
            "INSERT INTO executions(session_id,member_id,symbol,side,type,ref_id,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (cur.lastrowid, 999, symbols[0], "buy", "market", f"perf-{trade_date}", "placed", 0))


def _wire(monkeypatch, client):
    monkeypatch.setattr(performance.market, "_any_connected_member", lambda: 1)
    monkeypatch.setattr(performance.vault, "ensure_client", lambda mid: client)


def test_partial_fetch_serves_stale_good_series_and_backs_off(monkeypatch):
    from app.db import kv_set
    _seed_decided_session("2031-01-05", [f"PF{i}" for i in range(12)])  # 12+SPY → 2 batches
    good = {"start_date": "2031-01-05", "computed_at": 111, "n_sessions": 1,
            "points": [{"d": "2031-01-05", "club": 100.0, "spy": 100.0}],
            "club_pct": 0.0, "spy_pct": 0.0}
    kv_set(performance._CACHE_KEY, json.dumps({**good, "ts": 1, "ok": True}))  # good but EXPIRED
    bars = {s: {"2031-01-05": 100.0, "2031-01-06": 110.0} for s in ["SPY"] + [f"PF{i}" for i in range(12)]}
    client = _FailingClient(bars, fail_if=lambda batch: "SPY" not in batch)  # 2nd batch dies
    _wire(monkeypatch, client)
    out = performance.club_performance()
    assert out == good                      # stale GOOD served, degraded series discarded
    first_calls = client.calls
    assert performance.club_performance() == good
    assert client.calls == first_calls      # ok=False backoff — no refetch within retry TTL


def test_failed_first_fetch_caches_empty_with_backoff(monkeypatch):
    from app.db import db as _db
    with _db() as conn:
        conn.execute("DELETE FROM kv WHERE key=?", (performance._CACHE_KEY,))
    client = _FailingClient({}, fail_if=lambda batch: True)   # everything fails, no prior cache
    _wire(monkeypatch, client)
    out = performance.club_performance()
    assert out["points"] == [] and out["club_pct"] is None
    first_calls = client.calls
    assert client.calls > 0
    performance.club_performance()
    assert client.calls == first_calls      # no hammering during the backoff window


def test_clean_fetch_recomputes_and_caches_ok(monkeypatch):
    from app.db import db as _db
    with _db() as conn:
        conn.execute("DELETE FROM kv WHERE key=?", (performance._CACHE_KEY,))
        conn.execute("DELETE FROM vote_sessions WHERE trade_date LIKE '2031-%'")
        conn.execute("DELETE FROM executions WHERE ref_id LIKE 'perf-2031-%'")
    _seed_decided_session("2031-02-02", ["PFA"])
    bars = {"SPY": {"2031-02-02": 100.0, "2031-02-03": 101.0},
            "PFA": {"2031-02-02": 50.0, "2031-02-03": 55.0}}
    client = _FailingClient(bars)
    _wire(monkeypatch, client)
    out = performance.club_performance(force=True)
    assert [p["club"] for p in out["points"]] == [100.0, 110.0]
    assert [p["spy"] for p in out["points"]] == [100.0, 101.0]
    cached = json.loads(__import__("app.db", fromlist=["kv_get"]).kv_get(performance._CACHE_KEY))
    assert cached["ok"] is True and cached["points"]
