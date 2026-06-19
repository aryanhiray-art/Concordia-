"""Research-voter: turn a member's Robinhood research into a Concordia vote.

Pipeline:
  1. Read the member's Robinhood *research* — their stock watchlists (best-effort
     via their connected agentic token). Crypto/futures/option items are dropped.
     The result is cached as a snapshot so the vote still works if a later live
     read is unavailable; an explicit `override` symbol list bypasses the read.
  2. Keep only S&P 500 names (the votable universe) and take the top 20.
  3. Assign a **percentage distribution** — conviction-weighted: a name that
     appears in more of the member's lists gets proportionally more weight, with
     a per-name diversification cap so no single pick dominates. Sums to 100%.
  4. Cast it as the member's allocation on the open session (unless dry-run).

The distribution rule is intentionally simple and deterministic so the result is
explainable: weight ∝ (number of the member's lists the name appears in), then
capped at max(12%, 100%/N) and renormalised.
"""
from __future__ import annotations

import json

from ..db import audit, kv_get, kv_set
from ..robinhood import vault
from . import voting

CAP = 12.0     # max % on any single name before redistribution
TOP_N = 20     # most names the basket will hold


# ─── reading Robinhood research (watchlists) ─────────────────────────────────

def _live_research(member_id: int) -> list[dict]:
    """Aggregate the member's stock watchlists → [{symbol, appearances}], in order.

    appearances = how many of the member's lists the name is in (conviction).
    Raises if the account isn't connected or the MCP can't serve watchlists.
    """
    client = vault.ensure_client(member_id)
    wls = (client.get_watchlists().get("data") or {}).get("watchlists") or []
    counts: dict[str, int] = {}
    order: list[str] = []
    for wl in wls:
        name = (wl.get("display_name") or "").lower()
        allowed = wl.get("allowed_object_types") or []
        if name.startswith("options") or allowed == ["option_strategy"]:
            continue  # options watchlists carry no plain equities
        lid = wl.get("id")
        if not lid:
            continue
        items = (client.get_watchlist_items(lid).get("data") or {}).get("items") or []
        for it in items:
            if it.get("object_type") != "instrument":
                continue  # skip crypto (currency_pair), futures, indexes
            sym = (it.get("symbol") or "").upper().strip()
            if not sym:
                continue
            if sym not in counts:
                counts[sym] = 0
                order.append(sym)
            counts[sym] += 1
    return [{"symbol": s, "appearances": counts[s]} for s in order]


def _snap_key(member_id: int) -> str:
    return f"research:{member_id}"


def set_snapshot(member_id: int, research: list[dict]) -> None:
    kv_set(_snap_key(member_id), json.dumps(research))


def get_snapshot(member_id: int) -> list[dict]:
    raw = kv_get(_snap_key(member_id))
    try:
        return json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        return []


def research_for(member_id: int, override: list[str] | None = None) -> tuple[list[dict], str]:
    """Resolve the research set. Returns ([{symbol,appearances}], source).

    source ∈ {override, robinhood, snapshot, empty}. A successful live read
    refreshes the snapshot so the next run survives an MCP hiccup.
    """
    if override:
        seen, res = set(), []
        for s in override:
            u = str(s).upper().strip()
            if u and u not in seen:
                seen.add(u)
                res.append({"symbol": u, "appearances": 1})
        return res, "override"
    try:
        live = _live_research(member_id)
        if live:
            set_snapshot(member_id, live)
            return live, "robinhood"
    except Exception:  # noqa: BLE001 — fall back to the last good snapshot
        pass
    snap = get_snapshot(member_id)
    return snap, ("snapshot" if snap else "empty")


# ─── building the % distribution ─────────────────────────────────────────────

def _apply_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    """Clamp any name above `cap` and redistribute the excess to the rest, pro-rata."""
    w = dict(weights)
    for _ in range(50):
        over = {s: v for s, v in w.items() if v > cap + 1e-9}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for s in over:
            w[s] = cap
        under = {s: v for s, v in w.items() if v < cap - 1e-9}
        pool = sum(under.values())
        if pool <= 1e-9:
            break  # everything pinned at the cap; nothing to absorb the excess
        for s in under:
            w[s] += excess * (w[s] / pool)
    return w


def build_allocations(research: list[dict]) -> list[dict]:
    """[{symbol,appearances}] → [{symbol,weight}] over S&P 500, top 20, conviction-weighted."""
    uni = voting.universe_symbols()
    picks: list[tuple[str, int]] = []
    seen: set[str] = set()
    for r in research:
        s = str(r.get("symbol") or "").upper()
        if s in uni and s not in seen:
            seen.add(s)
            picks.append((s, max(1, int(r.get("appearances") or 1))))
    # rank by conviction (desc); stable sort preserves research order within a tier
    picks.sort(key=lambda x: -x[1])
    picks = picks[:TOP_N]
    if not picks:
        return []
    total_c = sum(c for _, c in picks)
    weights = {s: c / total_c * 100.0 for s, c in picks}
    eff_cap = max(CAP, 100.0 / len(picks) + 1e-6)  # keep the cap feasible for small baskets
    weights = _apply_cap(weights, eff_cap)
    order = [s for s, _ in picks]
    # WHOLE numbers that still sum to 100 — floor everything, then hand the
    # leftover points to the names with the largest fractional remainders.
    floored = {s: int(weights[s]) for s in order}
    drift = 100 - sum(floored.values())
    for s in sorted(order, key=lambda s: -(weights[s] - int(weights[s])))[:max(0, drift)]:
        floored[s] += 1
    return [{"symbol": s, "weight": floored[s]} for s in order if floored[s] > 0]


# ─── the orchestration the endpoints call ────────────────────────────────────

def research_vote(member_id: int, override: list[str] | None = None, dry: bool = False) -> dict:
    research, source = research_for(member_id, override)
    uni = voting.universe_symbols()
    eligible = [r["symbol"] for r in research if str(r["symbol"]).upper() in uni]
    excluded = [r["symbol"] for r in research if str(r["symbol"]).upper() not in uni]
    allocations = build_allocations(research)
    out: dict = {
        "source": source,
        "research_count": len(research),
        "eligible": eligible,
        "excluded": excluded,
        "allocations": allocations,
        "dry_run": dry,
    }
    if not allocations:
        out["error"] = (
            "no S&P 500 names in your Robinhood research"
            if source != "empty" else
            "could not read your Robinhood research (connect Robinhood, or pass symbols)"
        )
        return out
    if dry:
        return out
    session = voting.ensure_active_session()
    out["session"] = {"id": session["id"], "trade_date": session["trade_date"], "status": session["status"]}
    if session["status"] != "open":
        out["error"] = "voting is closed right now — try again when the next session opens"
        return out
    out["allocations"] = voting.set_member_allocations(session["id"], member_id, allocations)
    audit(f"member:{member_id}", "research_vote", session["trade_date"],
          {"source": source, "n": len(out["allocations"])})
    return out
