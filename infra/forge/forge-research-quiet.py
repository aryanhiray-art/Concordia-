#!/usr/bin/env python3
"""Hold forge's research jobs quiet while Concordia's bots are voting.

Concordia has no local Ollama — `concord-ollama-tunnel.service` pipes all 20 bot
inferences to THIS box, which also runs continuous BTC-lab research on 7 GB of
RAM. On 2026-08-24 that contention timed out the entire bot pass (0 of 20 voted)
and the club then traded real money on a one-voter tally. Bot votes outrank
research, so during each voting window the research units stand down.

This is a RECONCILER, not a pair of stop/start timers: it runs every few minutes
and drives the units to the state the current time implies. A missed tick (or a
reboot) self-corrects on the next one, so research can never be left off.
"""
from __future__ import annotations

import datetime
import subprocess
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Units to stand down. Research-only — nothing here places or manages a trade.
UNITS = ("research-warmer.service", "forge-btc-lab-daemon.service")

# (weekday, start, end) in ET; Mon=0. Mirrors concord-bot-vote.timer's attempts
# (Wed/Sat 09:00,13:00,17:00 and Mon 05:30,07:00,08:20) with a lead-in for model
# load and a tail past the service's 3000s cap. Monday is one block: the ballot
# closes 09:15 and a missed Monday is the costly one.
WINDOWS = (
    (0, "05:20", "09:20"),
    (2, "08:50", "10:05"), (2, "12:50", "14:05"), (2, "16:50", "18:05"),
    (5, "08:50", "10:05"), (5, "12:50", "14:05"), (5, "16:50", "18:05"),
)


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_quiet_window(now: datetime.datetime) -> bool:
    mins = now.hour * 60 + now.minute
    return any(now.weekday() == wd and _hhmm(a) <= mins < _hhmm(b) for wd, a, b in WINDOWS)


def _run(*argv: str) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:            # no systemctl (e.g. run on a dev laptop)
        return 127, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    now = datetime.datetime.now(ET)
    quiet = in_quiet_window(now)
    want = "stopped (Concordia bots voting)" if quiet else "running"
    print(f"[research-quiet] {now:%a %Y-%m-%d %H:%M %Z} → research should be {want}")

    for unit in UNITS:
        if _run("systemctl", "cat", unit)[0] != 0:
            print(f"  {unit}: not installed — skipping")
            continue
        active = _run("systemctl", "is-active", unit)[1] == "active"
        if quiet and active:
            rc, out = _run("systemctl", "stop", unit)
            print(f"  {unit}: stop → rc={rc} {out}")
        elif not quiet and not active:
            rc, out = _run("systemctl", "start", unit)
            print(f"  {unit}: start → rc={rc} {out}")
        else:
            print(f"  {unit}: already {'active' if active else 'stopped'} — no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
