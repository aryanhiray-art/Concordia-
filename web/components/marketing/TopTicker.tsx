"use client";

// The hairline marquee bar that opens the page. It is IN FLOW (not fixed), so
// it scrolls away with the hero and the nav docks into the space it leaves —
// see the transform comment in MktNav.
//
// Since 2026-09-09 (owner request) the bar is a live quote tape: real Alpaca
// market data for a FIXED list of S&P 500 mega-caps served by
// /api/public/tape. Two rules decide everything about how it renders:
//
//   1. The symbols are an editorial list hardcoded in the backend route —
//      NEVER the club's basket, which is private. Nothing about the club
//      feeds the tape, so it can't leak what the club holds.
//   2. No quotes, no tape. When the feed is down, empty, or stale past the
//      backend's honesty ceiling, the bar renders the CLAIMS strip below
//      instead — checkable sentences, not numbers nobody fetched (SPEC §0).

import { useEffect, useState } from "react";
import { fetchPublicTape, type TapeItem } from "@/lib/publicApi";
import { Marquee } from "./primitives";

/**
 * Ticker height in px. MktNav imports this to know how far to sit below the
 * bar at scroll 0 and how far to travel before it docks — the two components
 * are a single piece of chrome, so the constant lives with the element that
 * owns the space rather than being copied into both files.
 */
export const TICKER_H = 30;

// "ONE BALLOT PER CYCLE" deliberately, NOT "one vote per cycle": voting power
// is weighted by capital and accuracy (SPEC §3), so anything that reads as
// one-member-one-vote would be false. A ballot per cycle is the true claim.
const CLAIMS = [
  "INVITE-ONLY",
  "NON-CUSTODIAL",
  "YOUR OWN BROKERAGE ACCOUNT",
  "ONE BALLOT PER CYCLE",
  "S&P 500 UNIVERSE",
  "LIVE SINCE JUNE 2026",
  "FREE TO MEMBERS",
  "MONEY NEVER POOLED",
];

// The backend caches quotes for ~75s, so refetching faster than this buys
// nothing; slower and the tape drifts visibly behind the market during hours.
const REFRESH_MS = 90_000;

function Quote({ q }: { q: TapeItem }) {
  const up = q.chgPct > 0;
  const flat = q.chgPct === 0;
  return (
    <>
      <span className="text-[var(--mkt-muted)]">{q.sym}</span>
      {/* tnum: the whole bar is 11px mono microtype, and quote digits that
          shift width as prices tick would make the marquee shimmer. */}
      <span className="tnum ml-2 text-[var(--mkt-subtle)]">{q.price.toFixed(2)}</span>
      <span
        className="tnum ml-2"
        style={{ color: flat ? "var(--mkt-subtle)" : up ? "var(--mkt-green)" : "var(--mkt-red)" }}
      >
        {(up ? "+" : "") + q.chgPct.toFixed(2)}%
      </span>
    </>
  );
}

export function TopTicker() {
  // null = no usable quotes (yet, or at all) → render the claims strip. The
  // first paint is always claims: the swap happens when the fetch resolves,
  // which reads as the tape "coming online" rather than a bar that was empty.
  const [tape, setTape] = useState<TapeItem[] | null>(null);

  useEffect(() => {
    let dead = false;
    const load = () =>
      fetchPublicTape().then((items) => {
        // A shrunken answer (feed flapping, some symbols dropped) still
        // renders — 8+ quotes is a tape. Fewer reads as a glitch; fall back.
        if (!dead) setTape(items.length >= 8 ? items : null);
      });
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      dead = true;
      clearInterval(t);
    };
  }, []);

  const items = tape
    ? [
        // One framing chip per loop: the tape is the pool the club votes
        // FROM, and saying so is what keeps a reader from taking twelve
        // mega-caps for the club's (private) basket.
        <span key="label" className="text-[var(--mkt-muted)]">S&amp;P 500 — THE UNIVERSE</span>,
        ...tape.map((q) => <Quote key={q.sym} q={q} />),
      ]
    : CLAIMS;

  return (
    <div
      // box-sizing is border-box page-wide, so the 1px rule is inside TICKER_H
      // and MktNav's offset stays exact.
      className="w-full border-b border-[var(--mkt-hair)] bg-[var(--mkt-bg-2)]"
      style={{ height: TICKER_H }}
    >
      {/* The tape's track is ~1.6x the claims' width; scale the duration so
          the px/s crawl speed stays roughly the one the bar shipped with. */}
      <Marquee
        items={items}
        durationS={tape ? 92 : 58}
        className="flex h-full items-center"
      />
    </div>
  );
}

export default TopTicker;
