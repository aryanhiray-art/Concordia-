#!/usr/bin/env python3
"""Root-side gpt-5.5 narration of the latest post-cycle verification.

Self-contained ON PURPOSE: it imports NO concord app code and never opens the
concord DB — it only reads the JSON snapshot the in-service verifier
(executor.run → services.verify) wrote as the concord user. That keeps the
isolated DB owned by `concord` while this runs as root, which is the only user
whose ~/.openclaw owns the researcher_codex (gpt-5.5) agent.

systemd: concord-verify-report.timer → this, a few minutes after each execute.
ANY failure (stale snapshot, OpenClaw down) exits 0 without noise — the
authoritative deterministic report already rode in the execute summary + audit.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "verify_last.json"
MAX_AGE_SEC = 3 * 3600  # ignore a snapshot older than this run's window

OPENCLAW_BIN = os.environ.get("CONCORD_OPENCLAW_BIN", "openclaw")
OPENCLAW_AGENT = os.environ.get("CONCORD_OPENCLAW_AGENT", "researcher_codex")
OPENCLAW_MODEL = os.environ.get("CONCORD_OPENCLAW_MODEL", "gpt-5.5")
OPENCLAW_TIMEOUT = float(os.environ.get("CONCORD_OPENCLAW_TIMEOUT", "120"))

SYSTEM = (
    "You are the operations analyst for a small daily-vote stock club trading on "
    "each member's own Robinhood account. Given a DETERMINISTIC post-cycle "
    "verification of each member's live account, write a SHORT report: a headline "
    "verdict line then 2-5 tight bullets. Use ONLY the facts given; lead with any "
    "FAILURE (errored order, sell that didn't complete, account not flat when it "
    "should be); call ledger/phantom drift a bookkeeping note, not a trade "
    "failure; be concrete with symbols and dollars; no filler, no advice."
)


def _strip_preamble(out: str) -> str:
    lines, skip = out.splitlines(), 0
    for i, ln in enumerate(lines):
        st = ln.strip()
        if (st.startswith("├─") or "Missing env var" in ln or "Config warnings" in ln
                or st.startswith("│") or st.startswith("◇") or st.startswith("╭")
                or st.startswith("╰")):
            skip = i + 1
    return "\n".join(lines[skip:]).strip()


def main() -> int:
    try:
        snap = json.loads(SNAPSHOT.read_text())
    except Exception:  # noqa: BLE001
        print("[verify_narrate] no verification snapshot yet — nothing to narrate")
        return 0
    if time.time() - int(snap.get("at", 0)) > MAX_AGE_SEC:
        print(f"[verify_narrate] snapshot is stale ({snap.get('trade_date')}) — skipping")
        return 0

    report = snap.get("report", "")
    prompt = (SYSTEM + "\n\n=== DETERMINISTIC VERIFICATION (facts) ===\n"
              + f"overall verdict: {snap.get('verdict')}\n\n" + report)
    session_key = f"concord-verify-{int(time.time() * 1000)}"
    print(f"[verify_narrate] {snap.get('trade_date')} {snap.get('mode')} — verdict {snap.get('verdict')}")
    try:
        out = subprocess.run(
            [OPENCLAW_BIN, "agent", "--agent", OPENCLAW_AGENT,
             "--model", f"openai/{OPENCLAW_MODEL}", "--session-key", session_key, "-m", prompt],
            capture_output=True, text=True, timeout=OPENCLAW_TIMEOUT)
        if out.returncode != 0:
            print(f"[verify_narrate] openclaw rc={out.returncode} — deterministic report stands")
            return 0
        text = _strip_preamble(out.stdout or "")
        if text:
            print("\n=== gpt-5.5 report ===\n" + text)
    except Exception as e:  # noqa: BLE001
        print(f"[verify_narrate] narration failed ({type(e).__name__}) — deterministic report stands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
