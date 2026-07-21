"""Optional plain-English narration of a verification result via gpt-5.5.

Uses the PROVEN in-repo OpenClaw CLI contract (mirrors btc_lab/designer.py
`_openclaw_generate`): `openclaw agent --agent <id> --model openai/<model>
--session-key <fresh> -m <prompt>`, stripping the box-drawing preamble, with a
UNIQUE session key per call (a reused key let the codex transcript bloat and take
gpt-5.5 down). ANY failure → None, so the caller always falls back to the
deterministic report — the LLM is a narrator, never the source of truth.

NOTE: OpenClaw's agent config lives in the invoking user's ~/.openclaw. The
isolated `concord` service user has none, so this only produces text when run as
a user that owns the researcher_codex agent (root / the owner) — otherwise it
returns None and the deterministic report stands.
"""
from __future__ import annotations

import os
import subprocess
import time

OPENCLAW_BIN = os.environ.get("CONCORD_OPENCLAW_BIN", "openclaw")
OPENCLAW_AGENT = os.environ.get("CONCORD_OPENCLAW_AGENT", "researcher_codex")
OPENCLAW_MODEL = os.environ.get("CONCORD_OPENCLAW_MODEL", "gpt-5.5")
OPENCLAW_TIMEOUT = float(os.environ.get("CONCORD_OPENCLAW_TIMEOUT", "120"))

_SEQ = 0

_SYSTEM = (
    "You are the operations analyst for a small daily-vote stock club that trades "
    "on each member's own Robinhood account. You are given a DETERMINISTIC "
    "verification of each member's live account after a trading cycle. Write a "
    "SHORT report (a headline verdict line, then 2-5 tight bullets). Rules: use "
    "ONLY the facts given — invent nothing; lead with anything that FAILED (an "
    "errored order, a sell that didn't complete, an account that isn't flat when "
    "it should be); call ledger-drift/phantom names a bookkeeping note, not a "
    "trade failure; be concrete with symbols and dollars; no filler, no advice. "
    "If everything passed, say so plainly in one line."
)


def _strip_preamble(out: str) -> str:
    lines = out.splitlines()
    skip_until = 0
    for i, ln in enumerate(lines):
        st = ln.strip()
        if (st.startswith("├─") or "Missing env var" in ln or "Config warnings" in ln
                or st.startswith("│") or st.startswith("◇") or st.startswith("╭")
                or st.startswith("╰")):
            skip_until = i + 1
    return "\n".join(lines[skip_until:]).strip()


def narrate(verification: dict) -> str | None:
    """gpt-5.5 narration of a verify_all() result. None on ANY failure."""
    global _SEQ
    _SEQ += 1
    prompt = (
        _SYSTEM
        + "\n\n=== DETERMINISTIC VERIFICATION (facts) ===\n"
        + f"overall verdict: {verification.get('verdict')}\n\n"
        + verification.get("report", "")
    )
    session_key = f"concord-verify-{int(time.time() * 1000)}-{_SEQ}"
    try:
        out = subprocess.run(
            [OPENCLAW_BIN, "agent", "--agent", OPENCLAW_AGENT,
             "--model", f"openai/{OPENCLAW_MODEL}",
             "--session-key", session_key, "-m", prompt],
            capture_output=True, text=True, timeout=OPENCLAW_TIMEOUT)
        if out.returncode != 0:
            return None
        text = _strip_preamble(out.stdout or "")
        return text or None
    except Exception:  # noqa: BLE001 — never let narration break the caller
        return None
