"use client";

// The club feed (/club, below the power dial) — Reddit in MECHANICS, not
// look: members post and reply (one level, flat), everyone votes ±1, and the
// default ordering is the owner's stated rule, literally — author voting
// power first, then net votes, then recency. The server owns every rule
// (sanitizing, profanity, rate limits, ranking, permissions); this component
// renders and reports.
//
// Vote counts are taken from the vote endpoint's RESPONSE (authoritative),
// never predicted — an optimistic flip that the server rejects would show a
// tally the table doesn't hold.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SocialPost } from "@/lib/types";
import { Button, Card, Dots, EmptyState } from "@/components/ui";
import { StockLink } from "@/components/StockLink";

const BODY_MAX = 1000;

function ago(ts: number): string {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Body text with $TICKER cashtags wired into the research slide-over —
 *  the same affordance the ballot gives a symbol mention. */
function Body({ text }: { text: string }) {
  const parts = text.split(/(\$[A-Za-z]{1,5})(?![A-Za-z])/g);
  return (
    <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink">
      {parts.map((p, i) =>
        /^\$[A-Za-z]{1,5}$/.test(p) ? (
          <StockLink key={i} symbol={p.slice(1).toUpperCase()} unstyled
            className="font-semibold text-teal hover:underline">
            {p.toUpperCase()}
          </StockLink>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  );
}

function VoteRail({ post, onCounts }: {
  post: SocialPost;
  onCounts: (id: number, up: number, down: number, mine: 1 | -1 | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const net = post.up - post.down;
  const cast = async (dir: 1 | -1) => {
    if (busy) return;
    setBusy(true);
    try {
      // clicking your standing vote withdraws it (dir 0)
      const r = await api.socialVote(post.id, post.my_vote === dir ? 0 : dir);
      onCounts(post.id, r.up, r.down, r.my_vote);
    } catch {
      /* a 429 or a race — the standing counts remain correct */
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col items-center gap-0.5 pt-0.5" role="group" aria-label="votes">
      <button
        type="button"
        onClick={() => cast(1)}
        disabled={busy || post.deleted}
        aria-label="upvote"
        aria-pressed={post.my_vote === 1}
        className={`rounded px-1 text-[13px] leading-none transition-colors ${
          post.my_vote === 1 ? "text-green" : "text-subtle hover:text-ink"
        }`}
      >
        ▲
      </button>
      <span
        aria-label={`net score ${net}`}
        className={`tnum text-[12px] font-semibold ${net > 0 ? "text-green" : net < 0 ? "text-red" : "text-muted"}`}
      >
        {net}
      </span>
      <button
        type="button"
        onClick={() => cast(-1)}
        disabled={busy || post.deleted}
        aria-label="downvote"
        aria-pressed={post.my_vote === -1}
        className={`rounded px-1 text-[13px] leading-none transition-colors ${
          post.my_vote === -1 ? "text-red" : "text-subtle hover:text-ink"
        }`}
      >
        ▼
      </button>
    </div>
  );
}

function Composer({ placeholder, onDone, parentId, autoFocus }: {
  placeholder: string;
  parentId?: number;
  autoFocus?: boolean;
  onDone: () => void;
}) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    const text = body.trim();
    if (!text || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await api.socialPost(text, parentId ?? null);
      setBody("");
      onDone();
    } catch (e) {
      // the profanity/rate-limit reasons are written for humans — show them
      setErr(e instanceof ApiError ? e.message : "didn't go through — try again");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      {/* the house .input class carries the real panel bg + focus ring — the
          first draft's bg-panel/focus:border-hair-warm utilities were dead
          (no such Tailwind tokens) and left the field on UA defaults */}
      <textarea
        value={body}
        autoFocus={autoFocus}
        onChange={(e) => setBody(e.target.value.slice(0, BODY_MAX))}
        placeholder={placeholder}
        rows={parentId ? 2 : 3}
        className="input w-full resize-y !h-auto py-2.5 leading-relaxed"
      />
      <div className="mt-1.5 flex items-center justify-between gap-3">
        <span className="text-[11px] text-subtle tnum">{body.length}/{BODY_MAX}</span>
        <div className="flex items-center gap-3">
          {err && <span className="text-[12px] text-red">{err}</span>}
          <Button variant="go" busy={busy} disabled={!body.trim()} onClick={submit}>
            {parentId ? "Reply" : "Post"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function AuthorLine({ post }: { post: SocialPost }) {
  if (post.deleted || !post.author) {
    return <span className="text-[12px] italic text-subtle">removed</span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
      <span className="font-semibold text-muted">{post.author.name}</span>
      {post.author.share > 0 && (
        <span className="rounded-full border border-hair px-1.5 py-px text-[10px] uppercase tracking-[0.1em] text-teal tnum">
          {(post.author.share * 100).toFixed(1)}% say
        </span>
      )}
      <span className="text-subtle">{ago(post.created_at)}</span>
    </span>
  );
}

function PostCard({ post, meId, isAdmin, onCounts, onChanged }: {
  post: SocialPost;
  meId: number | null;
  isAdmin: boolean;
  onCounts: (id: number, up: number, down: number, mine: 1 | -1 | null) => void;
  onChanged: () => void;
}) {
  const [replying, setReplying] = useState(false);
  const canDelete = !post.deleted && (isAdmin || (meId != null && post.author?.id === meId));
  const del = async () => {
    if (!confirm("Delete this post? It can't be restored.")) return;
    try {
      await api.socialDelete(post.id);
      onChanged();
    } catch { /* 403/404 — the refetch shows the truth */ }
  };
  return (
    <article className="glass rise p-4 sm:p-5">
      <div className="flex gap-3">
        <VoteRail post={post} onCounts={onCounts} />
        <div className="min-w-0 flex-1">
          <AuthorLine post={post} />
          <div className="mt-1.5">
            {post.deleted
              ? <p className="text-[13px] italic text-subtle">[removed]</p>
              : <Body text={post.body} />}
          </div>
          <div className="mt-2 flex items-center gap-4 text-[11.5px]">
            <button type="button" onClick={() => setReplying((v) => !v)}
              className="label !text-[10px] text-subtle transition-colors hover:text-ink">
              {replying ? "cancel" : "reply"}
            </button>
            {canDelete && (
              <button type="button" onClick={del}
                className="label !text-[10px] text-subtle transition-colors hover:text-red">
                delete
              </button>
            )}
          </div>

          {replying && (
            <div className="mt-3">
              <Composer parentId={post.id} autoFocus placeholder="Make your case…"
                onDone={() => { setReplying(false); onChanged(); }} />
            </div>
          )}

          {(post.replies?.length ?? 0) > 0 && (
            <div className="mt-3 space-y-3 border-l border-hair pl-3.5">
              {post.replies!.map((r) => (
                <div key={r.id} className="flex gap-2.5">
                  <VoteRail post={r} onCounts={onCounts} />
                  <div className="min-w-0 flex-1">
                    <AuthorLine post={r} />
                    <div className="mt-1">
                      {r.deleted
                        ? <p className="text-[12.5px] italic text-subtle">[removed]</p>
                        : <Body text={r.body} />}
                    </div>
                    {(isAdmin || (meId != null && r.author?.id === meId)) && !r.deleted && (
                      <button type="button"
                        onClick={async () => {
                          if (!confirm("Delete this reply? It can't be restored.")) return;
                          try { await api.socialDelete(r.id); onChanged(); } catch {}
                        }}
                        className="label mt-1 !text-[10px] text-subtle transition-colors hover:text-red">
                        delete
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

export function ClubFeed() {
  const { member } = useAuth();
  const [sort, setSort] = useState<"top" | "new">("top");
  const [posts, setPosts] = useState<SocialPost[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [staleNote, setStaleNote] = useState(false);
  const seq = useRef(0);
  const havePosts = useRef(false);

  const load = useCallback(async (s: "top" | "new") => {
    const my = ++seq.current;
    try {
      const r = await api.socialFeed(s);
      if (my === seq.current) {
        setPosts(r.posts);
        setErr(null);
        setStaleNote(false);
        havePosts.current = true;
      }
    } catch (e) {
      if (my !== seq.current) return;
      // house convention: a failed BACKGROUND refresh keeps what's on
      // screen and says so quietly; only a first load shows the error card
      if (havePosts.current) setStaleNote(true);
      else setErr(e instanceof ApiError ? e.message : "couldn't load the feed");
    }
  }, []);

  useEffect(() => { load(sort); }, [sort, load]);

  // patch one post/reply's counts in place from the vote response
  const onCounts = useCallback((id: number, up: number, down: number, mine: 1 | -1 | null) => {
    setPosts((cur) => cur?.map((p) => {
      if (p.id === id) return { ...p, up, down, my_vote: mine };
      if (p.replies?.some((r) => r.id === id)) {
        return { ...p, replies: p.replies.map((r) => (r.id === id ? { ...r, up, down, my_vote: mine } : r)) };
      }
      return p;
    }) ?? cur);
  }, []);

  const isAdmin = member?.role === "owner" || member?.role === "admin";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="label">the club floor</span>
        <div className="seg" role="tablist" aria-label="Feed order">
          <button role="tab" aria-selected={sort === "top"} className={sort === "top" ? "on" : ""}
            onClick={() => setSort("top")}>
            Top
          </button>
          <button role="tab" aria-selected={sort === "new"} className={sort === "new" ? "on" : ""}
            onClick={() => setSort("new")}>
            New
          </button>
        </div>
      </div>

      <Card>
        <Composer placeholder="Say it to the club — theses, questions, victory laps…"
          onDone={() => load(sort)} />
        <p className="mt-2 text-[11px] leading-snug text-subtle">
          Louder voices float first — posts rank by the author&rsquo;s voting power, then votes,
          then recency. Keep it clean; the filter is not shy.
        </p>
      </Card>

      {staleNote && (
        <p className="text-[11.5px] text-subtle">refresh failed — showing older posts</p>
      )}
      {!posts && !err && (
        <Card>
          <div className="flex items-center gap-3 text-[12px] text-subtle">
            <Dots /> loading the floor…
          </div>
        </Card>
      )}
      {err && !posts && (
        <Card><p className="text-[13px] text-red">{err}</p></Card>
      )}
      {posts && posts.length === 0 && (
        <Card>
          <EmptyState
            icon={<span className="text-[15px]">▲</span>}
            title="Nothing on the floor yet"
            body="First post sets the tone — make it a thesis."
          />
        </Card>
      )}
      {(posts ?? []).map((p) => (
        <PostCard key={p.id} post={p} meId={member?.id ?? null} isAdmin={!!isAdmin}
          onCounts={onCounts} onChanged={() => load(sort)} />
      ))}
    </div>
  );
}
