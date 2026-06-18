#!/usr/bin/env python3
"""Concordia research-voter.

Reads your Robinhood research (your watchlists) on the Concordia server, keeps
only the S&P 500 names, takes the top 20, and casts a conviction-weighted vote —
authenticated with your Concordia API key. It never touches money; it only casts
your daily vote, exactly like the "Run now" button in the forge dashboard. Run it
on a schedule (cron) to vote your research automatically every day.

The percentage distribution is chosen for you: a name that appears in more of
your watchlists gets proportionally more weight, with a per-name cap (~12%) so no
single pick dominates. Weights sum to 100%.

Setup:
    export CONCORD_API_KEY=ck_live_...                       # from forge → Concordia → Program
    export CONCORD_API_URL=https://concord.5.78.193.177.sslip.io   # optional, this is the default

Usage:
    python research_voter.py             # cast today's vote from your research
    python research_voter.py --dry       # preview the basket, don't cast
    python research_voter.py AAPL MSFT NVDA   # vote an explicit list instead of the watchlist read
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://concord.5.78.193.177.sslip.io"


def main() -> None:
    ap = argparse.ArgumentParser(description="Cast your Concordia vote from your Robinhood research.")
    ap.add_argument("symbols", nargs="*", help="optional explicit tickers (skips the watchlist read)")
    ap.add_argument("--dry", action="store_true", help="preview the basket without casting")
    ap.add_argument("--url", default=os.environ.get("CONCORD_API_URL", DEFAULT_URL))
    ap.add_argument("--key", default=os.environ.get("CONCORD_API_KEY", ""))
    args = ap.parse_args()

    if not args.key:
        sys.exit("Set CONCORD_API_KEY to your Concordia API key (or pass --key).")

    payload: dict = {"dry_run": bool(args.dry)}
    if args.symbols:
        payload["symbols"] = [s.upper() for s in args.symbols]

    req = urllib.request.Request(
        f"{args.url.rstrip('/')}/api/program/research-vote",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"Connection failed: {e}")

    if res.get("error"):
        print("⚠ ", res["error"])
    allocs = res.get("allocations") or []
    cast = "" if res.get("dry_run") else " — vote cast ✓"
    if res.get("dry_run"):
        cast = " (dry-run, not cast)"
    print(f"research source: {res.get('source')} · {res.get('research_count', 0)} names "
          f"· {len(allocs)} in basket{cast}")
    for a in allocs:
        print(f"  {a['symbol']:<6} {a['weight']:>5.1f}%")
    if res.get("excluded"):
        print("excluded (not in S&P 500): " + ", ".join(res["excluded"]))


if __name__ == "__main__":
    main()
