"""Thin client for the local Ollama server + bot decision helper.

Mirrors the proven concordia-voter prompt shape: the agent is shown a universe
(ticker: price) and its current voting power, and must allocate 100% across 3-12
names, returning {"thesis", "picks":[{"ticker","pct"}]}. We force JSON output
(`format: "json"`) and defensively parse + normalize to valid universe tickers.
"""
from __future__ import annotations

import json

import requests

from ..config import settings


def probe(timeout: float = 2.0) -> dict:
    """Cheap LLM reachability check for the admin console — GET / on the Ollama
    server (it answers "Ollama is running" without loading a model, so this
    never triggers a multi-second model load the way /api/chat would).

    NEVER raises: the console must render "unreachable" as a status, not a 500 —
    on the live box the endpoint is an SSH tunnel to Forge, and a dropped tunnel
    is exactly the condition the owner opens this panel to diagnose.
    """
    try:
        r = requests.get(settings.ollama_url, timeout=timeout)
        return {"reachable": r.ok, "detail": f"HTTP {r.status_code} from {settings.ollama_url}"}
    except requests.RequestException as e:
        return {"reachable": False, "detail": f"{type(e).__name__}: {str(e)[:140]}"}


def chat(model: str, system: str, user: str, temperature: float = 0.5) -> str:
    """One-shot chat completion against local Ollama. Returns the raw text.

    keep_alive keeps the model resident between bots (they all share one model,
    so it loads once). Retries once on a transient transport error — under load
    Ollama can briefly refuse/timeout while a model loads.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": temperature},
    }
    last: Exception | None = None
    for attempt in range(2):
        try:
            r = requests.post(f"{settings.ollama_url}/api/chat", json=payload,
                              timeout=settings.ollama_timeout)
            r.raise_for_status()
            return (r.json().get("message") or {}).get("content") or ""
        except requests.RequestException as e:
            last = e
    raise last  # type: ignore[misc]


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                pass
    return {}


def normalize_picks(raw_picks, valid: set[str], max_names: int = 12) -> list[dict]:
    """Keep valid universe tickers, dedupe, clamp count, renormalize pcts to 100."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in (raw_picks or []):
        if not isinstance(p, dict):
            continue
        sym = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        if not sym or sym not in valid or sym in seen:
            continue
        try:
            pct = float(p.get("pct") or p.get("weight") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        seen.add(sym)
        out.append({"ticker": sym, "pct": max(0.0, pct)})
        if len(out) >= max_names:
            break
    total = sum(p["pct"] for p in out) or 0.0
    if out and total <= 0:                      # model gave no weights → equal-weight
        eq = round(100.0 / len(out), 2)
        for p in out:
            p["pct"] = eq
    elif total > 0:                             # renormalize to 100
        for p in out:
            p["pct"] = round(p["pct"] / total * 100, 2)
    return out


def decide(strategy: dict, tickers: list[str], prices: dict[str, float]) -> dict:
    """Run one bot's daily decision. Returns {thesis, picks:[{ticker,pct}]}.

    Raises on transport/parse failure so the caller can record the bot as failed
    for the day (it just abstains — no vote).
    """
    price_list = ", ".join(f"{t}: ${prices.get(t, '?')}" for t in tickers)
    user = (
        f"Universe (ticker: price): {price_list}.\n"
        "This is today's vote in a community trading club; your picks are weighted by your "
        "voting power and merged with every other member's into one basket that the club buys.\n"
        "Decide your allocation now and give a one-sentence thesis."
    )
    raw = chat(strategy["model"], strategy["system_prompt"], user,
               strategy.get("temperature", 0.5))
    data = _extract_json(raw)
    picks = normalize_picks(data.get("picks"), set(tickers))
    return {"thesis": str(data.get("thesis") or "")[:280], "picks": picks}
