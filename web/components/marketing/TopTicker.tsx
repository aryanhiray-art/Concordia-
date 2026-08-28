// The hairline marquee bar that opens the page. It is IN FLOW (not fixed), so
// it scrolls away with the hero and the nav docks into the space it leaves —
// see the transform comment in MktNav. Every phrase below is checkable against
// SPEC.md §2/§3; nothing here is a number and nothing implies a headcount.

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
const ITEMS = [
  "INVITE-ONLY",
  "NON-CUSTODIAL",
  "YOUR OWN BROKERAGE ACCOUNT",
  "ONE BALLOT PER CYCLE",
  "S&P 500 UNIVERSE",
  "LIVE SINCE JUNE 2026",
  "FREE TO MEMBERS",
  "MONEY NEVER POOLED",
];

export function TopTicker() {
  return (
    <div
      // box-sizing is border-box page-wide, so the 1px rule is inside TICKER_H
      // and MktNav's offset stays exact.
      className="w-full border-b border-[var(--mkt-hair)] bg-[var(--mkt-bg-2)]"
      style={{ height: TICKER_H }}
    >
      <Marquee items={ITEMS} durationS={58} className="flex h-full items-center" />
    </div>
  );
}

export default TopTicker;
