"use client";

// The marketing site's shared read of /api/public/stats, plus the two
// formatters that must agree wherever a figure from it is printed.
//
// This lived inside components/marketing/ProofStrip.tsx while that was the only
// consumer. It moved here once TrackRecord needed it too: a section component
// is the wrong place for a data layer — importing it dragged ProofStrip's whole
// client boundary along, and deleting or reordering a section would have been a
// cross-section change.

import { useEffect, useState } from "react";
import { FALLBACK_STATS, getPublicStats, type PublicStats } from "@/lib/publicApi";

/* ── Shared stats ──────────────────────────────────────────────────────────── */

// One request per page load, shared by every consumer. Both evidence sections
// mount together, so without this the page asks for the same cached blob twice.
// A failure resolves to FALLBACK_STATS and is deliberately cached too — a
// second section retrying an endpoint that is already down helps nobody, and
// the two sections landing on different values would be worse than either.
let inflight: Promise<PublicStats> | null = null;

function loadStats(): Promise<PublicStats> {
  const pending = inflight ?? (inflight = getPublicStats().catch(() => FALLBACK_STATS));
  return pending;
}

/** Live public stats, starting from the SPEC §2 fallback rather than from
 *  nothing.
 *
 *  Never a zero and never a spinner: a performance figure that reads 0 for
 *  200ms is a false statement about real money, and a skeleton where a return
 *  should be teaches the reader to distrust whatever replaces it. On failure
 *  the fallback simply stays — silently, because an error banner sitting over a
 *  set of correct figures would be the misleading part. */
export function usePublicStats(): PublicStats {
  const [stats, setStats] = useState<PublicStats>(FALLBACK_STATS);
  useEffect(() => {
    let alive = true;
    loadStats().then((s) => { if (alive) setStats(s); });
    return () => { alive = false; };
  }, []);
  return stats;
}

/* ── Dates ─────────────────────────────────────────────────────────────────── */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-06-17" → "17 Jun 2026" (or "Jun 2026"). Parsed by regex, never through
 *  `new Date(iso)`: that reads as UTC midnight and renders as the previous day
 *  for every viewer west of Greenwich, which would misdate the club's own
 *  inception on its own marketing page. Same lesson as lib/format.ts. */
export function fmtIso(iso: string, withDay = true): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!m) return iso;
  const mo = MONTHS[Number(m[2]) - 1] ?? m[2];
  return withDay ? `${Number(m[3])} ${mo} ${m[1]}` : `${mo} ${m[1]}`;
}

/** Settled signed percentage — rendered whole, never counted up to.
 *
 *  CountUp animates from 0, and this band sits above the fold, so its observer
 *  fires on load: a counted percentage paints "+0.00%" for the best part of a
 *  second. On a page whose entire argument is a return figure, that is not a
 *  loading state a reader can recognise — it is a flat year, stated in the
 *  club's own voice, before it corrects itself. */
export function pct(n: number): string {
  return `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)}%`;
}
