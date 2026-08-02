"use client";

// "Add to ballot" from a research surface (the full stock page and the panel).
//
// It reuses the ONE vote path — api.castVote, the same endpoint AllocSliders
// posts to — and always sends the member's WHOLE ballot, because
// POST /api/vote/today replaces the ballot rather than appending to it. So the
// existing allocations are merged in, never dropped and never zeroed.
//
// Weight the new pick gets (see mergePick):
//   • unallocated power ≥ an equal share → it takes that equal share,
//     100/(n+1) rounded, and NOTHING else on the ballot moves. A member holding
//     4 picks at 40% total keeps all four and abstains the rest.
//   • otherwise (the ballot is at/near 100%) → the new pick still gets the
//     equal share and the existing picks are scaled DOWN proportionally to fit,
//     each floored at 1% so no pick is ever silently renormalised to zero.
// The weight is always stated in the UI, and /vote stays the place to tune it.

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Alloc } from "@/lib/types";

/** Mirrors backend settings.max_picks (MAX_PICKS, default 20). */
const MAX_PICKS = 20;

export function mergePick(mine: Alloc[], symbol: string): { next: Alloc[]; weight: number } {
  const n = mine.length;
  const share = Math.max(1, Math.round(100 / (n + 1)));
  const used = mine.reduce((a, p) => a + (p.weight || 0), 0);
  if (100 - used >= share) {
    return {
      next: [...mine.map((p) => ({ symbol: p.symbol, weight: p.weight })), { symbol, weight: share }],
      weight: share,
    };
  }
  // Scale the existing picks into what's left, in whole percent. Floors alone
  // would quietly leak the rounding dust (19 picks → an 82% ballot), so the
  // remainder is handed back largest-fraction-first — the ballot keeps the
  // power it had, and no pick can round to 0.
  const room = 100 - share;
  const exact = mine.map((p) => ((p.weight || 0) * room) / (used || 1));
  // The 1% floor applies ONLY to picks that actually carry weight. Applying it
  // to every row invented power the member never allocated: a pick they had
  // deliberately dragged to 0% got pushed back to 1%, and with enough 0% rows
  // the ballot broke its own ceiling — measured 101% at nine zero-weight picks,
  // rising to 114% at eighteen. A 0% pick means "I don't back this", so it
  // stays 0 and only real weight is protected from rounding to nothing.
  const w = exact.map((v, i) => ((mine[i].weight || 0) > 0 ? Math.max(1, Math.floor(v)) : 0));
  let left = room - w.reduce((a, b) => a + b, 0);
  const byFraction = exact
    .map((v, i) => [v - Math.floor(v), i] as [number, number])
    .sort((a, b) => b[0] - a[0]);
  for (let k = 0; left > 0 && k < byFraction.length; k++, left--) w[byFraction[k][1]] += 1;
  const scaled = mine.map((p, i) => ({ symbol: p.symbol, weight: w[i] }));
  const taken = scaled.reduce((a, p) => a + p.weight, 0);
  const weight = Math.max(1, Math.min(share, 100 - taken));
  return { next: [...scaled, { symbol, weight }], weight };
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
