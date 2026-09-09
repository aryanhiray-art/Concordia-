"use client";

// /record — the proof page, and deliberately the driest one: zero custody
// copy, zero promises. The chart, its count, the fenced verification claim
// and the verbatim disclosure all live inside TrackRecord; this hero only
// frames whose record it is — the consensus's — and what the page refuses to
// be. Every figure on this page travels with the disclosure (SPEC §0/§2).

import { MktShell } from "../MktShell";
import { PageHero } from "../PageHero";
import { TrackRecord } from "../TrackRecord";
import { ClosingCTA } from "../ClosingCTA";

export function RecordPage() {
  return (
    <MktShell>
      <PageHero
        eyebrow="The record"
        title="What the votes actually picked."
        lead="Shown, not promised: a ledger of every decision the club has made, with the index and its benchmark one scroll below. A record — not a forecast."
      />
      <TrackRecord />
      <ClosingCTA />
    </MktShell>
  );
}
