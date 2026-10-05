"""The club feed — member posts, replies, and up/down votes (/club in the app).

Mechanics (owner spec, 2026-09-09): Reddit-like in mechanism, not look —
members post, everyone votes ±1, and the default ordering promotes AUTHOR
VOTING POWER first, then net votes, then recency. Power is read from
member_settings.voting_power (written by whichever POWER_MODEL/POWER_FORM is
live, so this stays form-agnostic), and nothing in this module ever writes
anything the power system reads — feed karma must never feed back into
reputation (the merit form's load-bearing invariant, reputation.py header).

Rules the handlers enforce, not the client:
  * Writes are SESSION-ONLY (require_session_member): a leaked API key can
    read the feed but must not speak or vote as the member. This also means
    bots never post — they authenticate with keys.
  * Bodies pass services/textguard: sanitized (control chars, whitespace,
    1000-char cap) and profanity-checked (owner: "filter out foul language").
    Rejected content is a 422 with a human reason, never silently edited.
  * Membership filters everywhere: a departed member's replies and votes
    stop counting immediately (active_clause), and their top-level posts
    render as authorless tombstones ONLY while other members' live replies
    hang off them — otherwise they vanish. Full account deletion hard-deletes
    everything except reply-anchoring tombstones (routes/members.py).
  * Posts soft-delete (deleted=1, body scrubbed) so reply threads keep their
    shape; the author or an owner/admin may delete. A tombstone with no live
    replies is not listed, and tombstones never rank by their author's power.
  * Per-member rate limits on posting and voting (in-process windows, same
    caveats as everywhere: single worker, reset on restart).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ratelimit
from ..config import settings
from ..db import audit, db
from ..services import membership
from ..services.textguard import profane, sanitize
from .deps import require_member, require_session_member

router = APIRouter(prefix="/api/social", tags=["social"])


def _discussion_gate(member: dict) -> None:
    """Discussion is OWNER-ONLY for now (owner call, 2026-10-05).

    Layered ON TOP of each route's existing dependency rather than replacing it,
    so the writes keep their session-only requirement (require_session_member) —
    swapping them to require_owner would have widened them to API-key auth.

    Hiding <ClubFeed/> in the UI is not hiding the feature: without this, any
    member could still read the whole feed and post into it with a bare fetch.
    /api/social/me is deliberately NOT gated — it is the voting-power dial's
    data, not discussion, and every member's /club page still renders it.
    """
    if settings.discussion_owner_only and member.get("role") != "owner":
        raise HTTPException(status_code=403, detail="discussion isn't open yet")

_ACTIVE = membership.active_clause("m")

# Post cadence: enough for a chatty evening, hostile to a runaway script.
_POST_BURST = (5, 300)        # 5 posts / 5 min
_POST_DAILY = (40, 86400)     # 40 posts / day
_VOTE_BURST = (60, 60)        # 60 vote actions / min


# ─── the dial: my power stats ────────────────────────────────────────────────

@router.get("/me")
def my_power_stats(member: dict = Depends(require_member)):
    """Everything the voting-power dial and its stats reveal need.

    Two contracts, both review-shaped (2026-09):
      * HONEST NULLS. member_settings columns default to 0, so a member who
        has never been through a power snapshot would otherwise see fabricated
        zeros about their money ("0.0% of the club's say", "$0 stake"). Every
        settings-derived figure is therefore GATED on a snapshot row existing;
        accuracy is additionally gated on having scored cycles. Null renders
        as an em-dash client-side, never a zero.
      * DELIBERATE WIDENING, and only this much: share and rank were
        previously visible admin-side only; serving a member their OWN share
        and rank is the point of the dial. The snapshot row's n_active and
        pool_usd are NOT served — a member headcount and the club's total
        power pool stay off member surfaces, matching the ballot's posture
        (voting.py keeps voter counts admin-only as an operational tell).
    """
    mid = member["id"]
    with db() as conn:
        s = conn.execute(
            "SELECT voting_power, voting_power_share, accuracy, accuracy_short, "
            "accuracy_long, cycles_participated, last_capital_usd "
            "FROM member_settings WHERE member_id=?", (mid,),
        ).fetchone()
        h = conn.execute(
            "SELECT week_start, share, rank_at_snapshot "
            "FROM voting_power_history WHERE member_id=? "
            "ORDER BY week_start DESC LIMIT 1", (mid,),
        ).fetchone()
    s = dict(s) if s else {}
    h = dict(h) if h else {}
    snapshotted = h.get("week_start") is not None
    share = s.get("voting_power_share") if snapshotted else None
    if share is None and snapshotted:
        share = h.get("share")
    cycles = s.get("cycles_participated")
    scored = bool(cycles)
    return {
        "power_usd": s.get("voting_power") if snapshotted else None,
        "share": share,
        "rank": h.get("rank_at_snapshot"),
        "as_of_week": h.get("week_start"),
        "accuracy": s.get("accuracy") if scored else None,
        "accuracy_short": s.get("accuracy_short") if scored else None,
        "accuracy_long": s.get("accuracy_long") if scored else None,
        "cycles_scored": cycles,
        "capital_usd": s.get("last_capital_usd") if snapshotted else None,
    }


# ─── the feed ────────────────────────────────────────────────────────────────

def _post_rows(conn, mid: int, sort: str, limit: int) -> list[dict]:
    """Top-level posts with vote tallies, my vote, and the author's power.

    ORDER for sort=top is the owner's stated rule, literally: author voting
    power first, then net votes, then recency. Power floors at 0 (a member
    with no snapshot ranks by votes alone) — deliberately NOT the ballot's
    MAX(power, 1.0) floor, which exists so unconnected members still count
    in a tally; here zero-power simply means "no boost".
    """
    # Tombstones (soft-deleted posts, and posts whose author has departed —
    # the row survives so OTHER members' replies aren't taken with them) are
    # listed only while they anchor at least one live reply, and always rank
    # at the bottom of "top": a deleted post must not sit pinned by its
    # author's power, and a childless tombstone is pure pollution. Replies to
    # deleted parents are rejected at write time, so a hidden childless
    # tombstone can never later grow a thread.
    order = (
        "ORDER BY (p.deleted=1 OR author_active=0) ASC, "
        "CASE WHEN p.deleted=1 OR author_active=0 THEN 0 "
        "ELSE COALESCE(ms.voting_power, 0) END DESC, net DESC, p.created_at DESC"
        if sort == "top"
        else "ORDER BY p.created_at DESC"
    )
    # Vote tallies count ACTIVE voters only — the ballot excludes departed
    # identities at tally time, and the feed follows it (their vote rows stay
    # for reinstatement; they just stop counting).
    rows = conn.execute(
        f"""
        SELECT p.id, p.member_id, p.body, p.created_at, p.deleted,
               m.display_name AS author_name,
               CASE WHEN {_ACTIVE} THEN 1 ELSE 0 END AS author_active,
               COALESCE(ms.voting_power_share, 0) AS author_share,
               COALESCE(SUM(CASE WHEN v.dir=1  THEN 1 ELSE 0 END), 0) AS up,
               COALESCE(SUM(CASE WHEN v.dir=-1 THEN 1 ELSE 0 END), 0) AS down,
               COALESCE(SUM(v.dir), 0) AS net,
               (SELECT dir FROM club_post_votes WHERE post_id=p.id AND member_id=?) AS my_vote
        FROM club_posts p
        JOIN members m ON m.id = p.member_id
        LEFT JOIN member_settings ms ON ms.member_id = p.member_id
        LEFT JOIN club_post_votes v ON v.post_id = p.id
             AND EXISTS (SELECT 1 FROM members vm WHERE vm.id = v.member_id
                         AND {membership.active_clause("vm")})
        WHERE p.parent_id IS NULL
          AND (
            (p.deleted = 0 AND {_ACTIVE})
            OR EXISTS (SELECT 1 FROM club_posts r
                       JOIN members rm ON rm.id = r.member_id
                       WHERE r.parent_id = p.id AND r.deleted = 0
                         AND {membership.active_clause("rm")})
          )
        GROUP BY p.id
        {order}
        LIMIT ?
        """,
        (mid, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _reply_rows(conn, mid: int, parent_ids: list[int]) -> dict[int, list[dict]]:
    """Replies for the given posts, oldest first (a conversation reads down)."""
    if not parent_ids:
        return {}
    marks = ",".join("?" * len(parent_ids))
    rows = conn.execute(
        f"""
        SELECT p.id, p.parent_id, p.member_id, p.body, p.created_at, p.deleted,
               m.display_name AS author_name,
               COALESCE(ms.voting_power_share, 0) AS author_share,
               COALESCE(SUM(CASE WHEN v.dir=1  THEN 1 ELSE 0 END), 0) AS up,
               COALESCE(SUM(CASE WHEN v.dir=-1 THEN 1 ELSE 0 END), 0) AS down,
               (SELECT dir FROM club_post_votes WHERE post_id=p.id AND member_id=?) AS my_vote
        FROM club_posts p
        JOIN members m ON m.id = p.member_id
        LEFT JOIN member_settings ms ON ms.member_id = p.member_id
        LEFT JOIN club_post_votes v ON v.post_id = p.id
             AND EXISTS (SELECT 1 FROM members vm WHERE vm.id = v.member_id
                         AND {membership.active_clause("vm")})
        WHERE p.parent_id IN ({marks}) AND {_ACTIVE}
        GROUP BY p.id
        ORDER BY p.created_at ASC
        """,
        (mid, *parent_ids),
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["parent_id"], []).append(_shape(dict(r)))
    return out


def _shape(row: dict) -> dict:
    """The wire shape for one post/reply. A soft-deleted row — or one whose
    author has departed — keeps its slot (replies need their parent) but
    sheds its content and author. Share is rounded to 3 decimals (0.1%
    granularity): coarse enough that nobody back-computes a fellow member's
    stake in dollars from the chip, fine enough to explain the ranking."""
    deleted = bool(row["deleted"]) or not row.get("author_active", 1)
    return {
        "id": row["id"],
        "author": None if deleted else {
            "id": row["member_id"],
            "name": row["author_name"],
            "share": round(float(row["author_share"] or 0.0), 3),
        },
        "body": "" if deleted else row["body"],
        "created_at": row["created_at"],
        "deleted": deleted,
        "up": int(row["up"]),
        "down": int(row["down"]),
        "my_vote": row["my_vote"],
    }


@router.get("/feed")
def feed(sort: str = "top", limit: int = 100, member: dict = Depends(require_member)):
    _discussion_gate(member)
    if sort not in ("top", "new"):
        raise HTTPException(status_code=422, detail="sort must be 'top' or 'new'")
    limit = max(1, min(int(limit), 200))
    mid = member["id"]
    with db() as conn:
        posts = _post_rows(conn, mid, sort, limit)
        replies = _reply_rows(conn, mid, [p["id"] for p in posts])
    shaped = []
    for p in posts:
        item = _shape(p)
        item["net"] = int(p["net"])
        item["replies"] = replies.get(p["id"], [])
        shaped.append(item)
    return {"posts": shaped, "sort": sort}


# ─── writing ────────────────────────────────────────────────────────────────

class PostBody(BaseModel):
    # 4000 is the pydantic backstop; textguard caps the sanitized body at 1000.
    body: str = Field(min_length=1, max_length=4000)
    parent_id: int | None = None


@router.post("/posts")
def create_post(payload: PostBody, member: dict = Depends(require_session_member)):
    _discussion_gate(member)
    mid = member["id"]
    if ratelimit.too_many(f"clubpost:{mid}", *_POST_BURST) or \
       ratelimit.too_many(f"clubpostday:{mid}", *_POST_DAILY):
        raise HTTPException(status_code=429, detail="posting too fast — give it a minute")
    # Charge the window BEFORE the slow work (sanitize + DB round-trip):
    # these handlers run on the threadpool, so check-then-record-after lets N
    # concurrent requests all pass the check before any record lands —
    # measured at ~40 posts through a 5-post window. A rejected post costs a
    # slot; that is the cheaper failure. (auth.py already uses this order.)
    ratelimit.record(f"clubpost:{mid}")
    ratelimit.record(f"clubpostday:{mid}")

    body = sanitize(payload.body)
    if not body:
        raise HTTPException(status_code=422, detail="the post is empty once cleaned up")
    if profane(body):
        raise HTTPException(
            status_code=422,
            detail="that reads as foul language — the club feed keeps it clean; edit and repost",
        )

    with db() as conn:
        if payload.parent_id is not None:
            parent = conn.execute(
                "SELECT id, parent_id, deleted FROM club_posts WHERE id=?",
                (payload.parent_id,),
            ).fetchone()
            if not parent or parent["deleted"]:
                raise HTTPException(status_code=404, detail="that post is gone")
            if parent["parent_id"] is not None:
                # flat threads: replies attach to the top-level post only
                raise HTTPException(status_code=422, detail="replies can't nest — reply to the post")
        cur = conn.execute(
            "INSERT INTO club_posts(member_id, parent_id, body, created_at) VALUES(?,?,?,?)",
            (mid, payload.parent_id, body, int(time.time())),
        )
        pid = cur.lastrowid
    audit(f"member:{mid}", "club_post", f"post:{pid}",
          {"parent_id": payload.parent_id, "chars": len(body)})
    return {"ok": True, "id": pid}


class VoteBody(BaseModel):
    # +1 up, -1 down, 0 withdraws the vote
    dir: int = Field(ge=-1, le=1)


@router.post("/posts/{post_id}/vote")
def vote_post(post_id: int, payload: VoteBody, member: dict = Depends(require_session_member)):
    _discussion_gate(member)
    mid = member["id"]
    if ratelimit.too_many(f"clubvote:{mid}", *_VOTE_BURST):
        raise HTTPException(status_code=429, detail="voting too fast — give it a minute")
    ratelimit.record(f"clubvote:{mid}")  # charge before the DB work — see create_post
    with db() as conn:
        row = conn.execute(
            "SELECT id, deleted FROM club_posts WHERE id=?", (post_id,),
        ).fetchone()
        # A deleted post takes no NEW votes, but a member may always WITHDRAW
        # a standing vote from a tombstone — otherwise their vote is frozen
        # into a tally they can still see.
        if not row or (row["deleted"] and payload.dir != 0):
            raise HTTPException(status_code=404, detail="that post is gone")
        if payload.dir == 0:
            conn.execute(
                "DELETE FROM club_post_votes WHERE post_id=? AND member_id=?",
                (post_id, mid),
            )
        else:
            conn.execute(
                "INSERT INTO club_post_votes(post_id, member_id, dir, created_at) "
                "VALUES(?,?,?,?) ON CONFLICT(post_id, member_id) "
                "DO UPDATE SET dir=excluded.dir, created_at=excluded.created_at",
                (post_id, mid, payload.dir, int(time.time())),
            )
        # active voters only — the same rule the feed listing applies
        up, down = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN v.dir=1 THEN 1 ELSE 0 END),0), "
            f"COALESCE(SUM(CASE WHEN v.dir=-1 THEN 1 ELSE 0 END),0) "
            f"FROM club_post_votes v JOIN members vm ON vm.id = v.member_id "
            f"WHERE v.post_id=? AND {membership.active_clause('vm')}", (post_id,),
        ).fetchone()
    return {"ok": True, "up": int(up), "down": int(down), "my_vote": payload.dir or None}


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, member: dict = Depends(require_session_member)):
    _discussion_gate(member)
    mid = member["id"]
    is_admin = member.get("role") in ("owner", "admin")
    with db() as conn:
        row = conn.execute(
            "SELECT id, member_id, deleted FROM club_posts WHERE id=?", (post_id,),
        ).fetchone()
        if not row or row["deleted"]:
            raise HTTPException(status_code=404, detail="that post is gone")
        if row["member_id"] != mid and not is_admin:
            raise HTTPException(status_code=403, detail="not your post")
        # Soft delete: the row keeps the thread's shape, sheds its content.
        conn.execute(
            "UPDATE club_posts SET deleted=1, body='' WHERE id=?", (post_id,),
        )
    audit(f"member:{mid}", "club_post_delete", f"post:{post_id}",
          {"as_admin": is_admin and row["member_id"] != mid})
    return {"ok": True}
