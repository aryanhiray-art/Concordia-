"""Who is actually IN the club — the single membership predicate.

Before v2 there was no lifecycle at all: `members` had no left_at and no
is_active, so the only departure signal was self-service deletion ANONYMIZING
the row in place to `deleted-N@deleted.invalid`. Three services
(adminview, metrics, watchdog) filtered on that email pattern by hand; four
others (reputation.snapshot_power_all, reputation.score_cycle, voting._tally_rows,
GET /api/admin/overview) did not filter at all. The consequences were real:
a departed member kept a slice of the voting-power denominator, and their
already-cast ballot could still land a ticker in the traded basket.

`left_at` is set ONLY by an explicit human action — self-service DELETE /api/me,
or the owner-side removal endpoint. It is NEVER inferred from capital, from
rh_accounts.status, from opted_in, or from a failed balance read. Departure is a
statement about MEMBERSHIP; being broke, opted out, disconnected or temporarily
unreadable are statements about FUNDING, and conflating them would let a
transient Robinhood error silently evict a member from the club.

Leaving never rewrites history. A departed member's accuracy, cycles and past
voting_power_history rows are left exactly as they were — they simply stop being
counted. That is what makes re-instatement (and any future audit) possible.
"""
from __future__ import annotations

import time

from ..db import audit, db

# Rows anonymized in place by self-service deletion, kept for referential
# integrity (votes/executions still point at them). Matched by the same
# fingerprint adminview.py and metrics.py already trust.
_ANON_EMAIL = "deleted-%@deleted.invalid"


class AccountTakenOver(Exception):
    """Reinstatement would put two active members on one brokerage account."""


def active_clause(alias: str = "m") -> str:
    """SQL predicate for 'this member counts', for use in a WHERE/JOIN.

    Takes the table alias so it can be dropped into any existing join without
    restructuring the query. Both halves are needed: left_at covers members who
    left going forward, the email pattern covers rows anonymized before left_at
    existed (the migration backfills those, but the pattern stays as a belt-and-
    braces guard — an anonymized row must never vote again even if left_at is
    somehow cleared).
    """
    return f"{alias}.left_at IS NULL AND {alias}.email NOT LIKE '{_ANON_EMAIL}'"


def is_active(member_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            f"SELECT 1 FROM members m WHERE m.id=? AND {active_clause('m')}", (member_id,)
        ).fetchone()
    return row is not None


def active_ids(include_bots: bool = True) -> list[int]:
    clause = active_clause("m")
    if not include_bots:
        clause += " AND COALESCE(m.is_bot,0)=0"
    with db() as conn:
        return [r["id"] for r in conn.execute(
            f"SELECT m.id FROM members m WHERE {clause} ORDER BY m.id").fetchall()]


def set_left(member_id: int, actor: str | None, reason: str = "left") -> bool:
    """Mark a member departed. Idempotent; returns True only on the transition.

    Deliberately does NOT touch accuracy, cycles_participated or any history row.
    The member becomes inert at the next snapshot by falling out of the active
    set — nothing is reset, so the numbers stay explainable and a reinstatement
    is a one-column change rather than a reconstruction.
    """
    now = int(time.time())
    with db() as conn:
        # session_version is bumped with the same UPDATE so every live session
        # and API token for this member dies immediately. deps.py checks the
        # token's `ver` against this column; without the bump an owner-removed
        # member keeps a working session and can carry on using the app after
        # being removed. delete_account has always done this — removal must too.
        cur = conn.execute(
            "UPDATE members SET left_at=?, session_version=session_version+1 "
            "WHERE id=? AND left_at IS NULL", (now, member_id))
        changed = cur.rowcount == 1
    if changed:
        audit(actor, "member_left", str(member_id), {"reason": reason, "left_at": now})
    return changed


def account_clash(member_id: int) -> int | None:
    """The id of an ACTIVE member already holding this member's brokerage
    account, if any. Used to gate reinstatement: while a member is away their
    account is claimable by someone else, so coming back can put two active
    members on one balance — each resolving the same dollars as committed
    capital, permanently doubling one person's weight."""
    with db() as conn:
        row = conn.execute(
            "SELECT b.member_id FROM rh_accounts a JOIN rh_accounts b "
            "  ON b.account_number = a.account_number AND b.member_id != a.member_id "
            "JOIN members m ON m.id = b.member_id "
            f"WHERE a.member_id = ? AND a.account_number IS NOT NULL AND {active_clause('m')}",
            (member_id,)).fetchone()
    return row["member_id"] if row else None


def clear_left(member_id: int, actor: str | None) -> bool:
    """Reinstate a departed member with their record intact. Returns True on the
    transition. Anonymized (self-deleted) rows are NOT reinstatable — their
    identity is gone, so there is nobody to reinstate."""
    clash = account_clash(member_id)
    if clash is not None:
        audit(actor, "member_reinstate_blocked", str(member_id),
              {"reason": "brokerage account now held by another member",
               "held_by": clash})
        raise AccountTakenOver(
            f"another member (id {clash}) now holds this member's brokerage account — "
            "resolve that before reinstating")
    with db() as conn:
        cur = conn.execute(
            "UPDATE members SET left_at=NULL WHERE id=? AND left_at IS NOT NULL "
            f"AND email NOT LIKE '{_ANON_EMAIL}'", (member_id,))
        changed = cur.rowcount == 1
    if changed:
        audit(actor, "member_reinstated", str(member_id), None)
    return changed
