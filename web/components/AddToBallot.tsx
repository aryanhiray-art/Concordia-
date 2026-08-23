"use client";

// "Add to ballot" from a research surface (the full stock page and the panel).
//
// It reuses the ONE vote path — api.castVote, the same endpoint AllocSliders
// posts to — and always sends the member's WHOLE ballot, because
// POST /api/vote/today replaces the ballot rather than appending to it. So the
// existing allocations are merged in, never dropped and never zeroed.
//
// Weight the new pick gets (see mergePick): the unallocated remainder, and
// nothing else — existing picks never move. 70% allocated → the new name gets
// 30%; already at 100% → it joins at 0% and the member slides weight over on
// /vote. The weight is always stated in the UI before the click.

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Alloc } from "@/lib/types";

/** Mirrors backend settings.max_picks (MAX_PICKS, default 20). */
const MAX_PICKS = 20;

export function mergePick(mine: Alloc[], symbol: string): { next: Alloc[]; weight: number } {
  // The new pick takes ONLY the unallocated remainder — existing picks are
  // never touched (owner rule). A ballot at 70% gains the new name at 30%; a
  // ballot already at 100% gains it at 0%, and the member slides weight over
  // themselves if they want it funded. The earlier version scaled everyone
  // down to make room, which re-weighted picks the member had set on purpose.
  const used = mine.reduce((a, p) => a + (p.weight || 0), 0);
  const weight = Math.max(0, Math.min(100, Math.round(100 - used)));
  return {
    // thesis passes through untouched: POST /api/vote/today REPLACES the whole
    // ballot, so dropping it here would silently erase saved rationales.
    next: [...mine.map((p) => ({ symbol: p.symbol, weight: p.weight, thesis: p.thesis ?? null })),
           { symbol, weight }],
    weight,
  };
}

const BTN = "inline-flex flex-none items-center gap-2 rounded-[11px] border px-4 py-2.5 " +
  "text-[13px] font-semibold transition disabled:cursor-default disabled:opacity-45";
const LIVE = "border-teal/25 bg-teal/[0.08] text-teal hover:bg-teal/[0.14]";
const DEAD = "border-hair bg-white/[0.03] text-muted";

export function AddToBallot({ symbol, inUniverse }: { symbol: string; inUniverse: boolean }) {
  const { member, session, refreshSession } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const mine = session?.my_picks || [];
  const on = mine.find((p) => p.symbol === symbol);
  const optedIn = Number(member?.settings?.opted_in) === 1;

  // already voted for it — show the standing weight instead of a second control
  if (on) {
    return (
      <div className="flex flex-wrap items-center gap-2.5 text-[13px]">
        <span className="inline-flex items-center gap-1.5 rounded-[11px] border border-green/30 bg-green/[0.08] px-3.5 py-2.5 font-semibold text-green">
          ✓ On your ballot · <b className="tnum">{Math.round(on.weight)}%</b>
        </span>
        <Link href="/vote" className="text-[12.5px] font-semibold text-teal hover:underline">adjust</Link>
      </div>
    );
  }

  const blocked: React.ReactNode =
    !inUniverse ? "not in the S&P 500 universe"
      : !session ? "loading your ballot"
        : session.status !== "open" ? "voting closed"
          : !optedIn ? <>participation is off — <Link href="/account" className="font-semibold text-teal hover:underline">turn it on</Link></>
            : mine.length >= MAX_PICKS ? `ballot is full (${MAX_PICKS} picks)`
              : null;

  async function add() {
    if (!session) return;
    setBusy(true); setErr(null);
    try {
      const { next } = mergePick(mine, symbol);
      await api.castVote(next);
      await refreshSession();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "couldn't add this pick");
    } finally { setBusy(false); }
  }

  const { weight } = mergePick(mine, symbol);
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      <button onClick={add} disabled={!!blocked || busy}
        className={`${BTN} ${blocked ? DEAD : LIVE}`}>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
        {busy ? "adding…" : "Add to ballot"}
      </button>
      <span className="text-[12px] text-subtle">
        {blocked ?? <>at <b className="text-muted tnum">{weight}%</b> of your power</>}
      </span>
      {err && <span className="w-full text-[12px] text-red">⚠ not added — {err}</span>}
    </div>
  );
}
