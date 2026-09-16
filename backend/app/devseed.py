"""Create the local sandbox member that lib/devAutoLogin.ts signs in as.

    cd backend && ./.venv/bin/python -m app.devseed

Run it whenever the demo database is recreated and `npm run dev` starts landing
on the sign-in card again. It creates (or repairs) one verified member, attaches
the fake DEMO_MODE brokerage account, and gives them a committed stake plus two
voting-power snapshots — enough state that every screen renders its populated
branch instead of its empty one.

REFUSES TO RUN outside the fake-broker sandbox. Every guard below is about the
same thing: this writes a known password into a members table, and the members
table on the production box holds people's real encrypted broker tokens.
"""
from __future__ import annotations

import sys
import time

from .config import settings
from .db import db, init_db
from .security import hash_password

EMAIL = "uicheck@demo.local"
PASSWORD = "demo-local-only-1"        # matches web/lib/devAutoLogin.ts
DISPLAY_NAME = "Sandbox Member"


def _refuse(reason: str) -> None:
    print(f"devseed: refusing to run — {reason}", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    if not settings.demo_mode:
        _refuse("DEMO_MODE is not set (this is not the fake-broker sandbox)")
    if settings.execution_mode == "live":
        _refuse("EXECUTION_MODE=live")
    if "demo" not in settings.db_path:
        _refuse(f"database path does not look like a sandbox: {settings.db_path}")

    init_db()
    now = int(time.time())

    with db() as conn:
        row = conn.execute("SELECT id FROM members WHERE email=?", (EMAIL,)).fetchone()
        if row:
            mid = int(row["id"])
            conn.execute(
                "UPDATE members SET password_hash=?, email_verified_at=?, display_name=?, "
                "left_at=NULL WHERE id=?",
                (hash_password(PASSWORD), now, DISPLAY_NAME, mid))
        else:
            cur = conn.execute(
                "INSERT INTO members(email, display_name, role, invite_code_used, password_hash, "
                "session_version, created_at, is_bot, email_verified_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (EMAIL, DISPLAY_NAME, "member", "demo", hash_password(PASSWORD), 1, now, 0, now))
            mid = int(cur.lastrowid)

        # Opted in, committed, and with a voting power the header can print.
        conn.execute(
            "INSERT INTO member_settings(member_id, opted_in, voting_power, "
            "alloc_type, alloc_value, accuracy, cycles_participated, updated_at) "
            "VALUES(?,1,486.20,'fixed',500,0.5,6,?) "
            "ON CONFLICT(member_id) DO UPDATE SET opted_in=1, "
            "voting_power=486.20, alloc_type='fixed', alloc_value=500, updated_at=excluded.updated_at",
            (mid, now))

        # Borrow the fake broker credentials the demo fixtures already use, so
        # this member is "connected" without going through the OAuth dance.
        src = conn.execute(
            "SELECT account_number, agentic_allowed, enc_tokens FROM rh_accounts "
            "WHERE status='connected' AND member_id<>? LIMIT 1", (mid,)).fetchone()
        if src:
            conn.execute(
                "INSERT INTO rh_accounts(member_id, account_number, agentic_allowed, enc_tokens, "
                "connected_at, last_refresh_at, status) VALUES(?,?,?,?,?,?, 'connected') "
                "ON CONFLICT(member_id) DO UPDATE SET status='connected', "
                "account_number=excluded.account_number, enc_tokens=excluded.enc_tokens",
                (mid, src["account_number"], src["agentic_allowed"], src["enc_tokens"], now, now))
        else:
            print("devseed: no connected demo brokerage row to copy — connect once in the UI "
                  "and re-run to get the populated screens.", file=sys.stderr)

        # Two snapshots so the /vote header shows a real figure AND an as-of
        # week — with share/rank/pool populated so the /club dial's stats
        # reveal renders its full branch in the sandbox.
        conn.execute("DELETE FROM voting_power_history WHERE member_id=?", (mid,))
        conn.executemany(
            "INSERT INTO voting_power_history(member_id, week_start, power, equity, prev_power, "
            "delta, created_at, share, rank_at_snapshot, n_active, pool_usd) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(mid, "2026-08-09", 412.50, 500.0, None, None, now - 1209600, 0.041, 9, 24, 10060.0),
             (mid, "2026-08-23", 486.20, 500.0, 412.50, 73.70, now - 259200, 0.049, 7, 24, 9922.0)])
        conn.execute(
            "UPDATE member_settings SET voting_power_share=0.049, last_capital_usd=500 "
            "WHERE member_id=?", (mid,))

        # A couple of feed posts so /club opens populated. Idempotent: the
        # sandbox member's posts are wiped and re-seeded each run.
        conn.execute(
            "DELETE FROM club_post_votes WHERE post_id IN "
            "(SELECT id FROM club_posts WHERE member_id=?)", (mid,))
        conn.execute("DELETE FROM club_posts WHERE member_id=?", (mid,))
        cur = conn.execute(
            "INSERT INTO club_posts(member_id, parent_id, body, created_at) VALUES(?,?,?,?)",
            (mid, None,
             "Made the case for MSFT in my thesis this cycle — cloud spend guidance "
             "was the whole story. Curious if anyone read it differently.",
             now - 5400))
        first_post = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO club_posts(member_id, parent_id, body, created_at) VALUES(?,?,?,?)",
            (mid, first_post,
             "Replying to my own post to show a thread — the demo is a sandbox.",
             now - 3600))
        conn.execute(
            "INSERT INTO club_posts(member_id, parent_id, body, created_at) VALUES(?,?,?,?)",
            (mid, None,
             "Reminder: ballots close 09:15 ET. If you have conviction, say it in a "
             "thesis — the tally shows the case next to the number.",
             now - 900))

    print(f"devseed: member #{mid} ready — {EMAIL} / {PASSWORD}")
    print("devseed: `npm run dev` will now sign in automatically; add ?signin to skip.")


if __name__ == "__main__":
    main()
