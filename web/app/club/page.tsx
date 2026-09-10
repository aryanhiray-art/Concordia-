"use client";

// /club — the club's own room: the voting-power dial (click it for your
// statistics) and the power-weighted floor beneath.
//
// Composed in the app's OWN formula — PageHeader + glass cards, exactly like
// /vote and /history (owner call, 2026-09-10: "revert the concordia style to
// the previous style"). The first cut opened with a marketing-style centered
// hero ("your signal" eyebrow, freestanding dial); the feature keeps its
// mechanics and motion, the page keeps the app's grammar.

import { ClubPowerDial } from "@/components/ClubPowerDial";
import { ClubFeed } from "@/components/ClubFeed";
import { PageHeader } from "@/components/PageHeader";

export default function ClubPage() {
  return (
    <div className="stagger space-y-5">
      <PageHeader
        title="Club"
        lead="Your voting-power signal, and the club's floor — posts ranked by the author's say first, then votes."
      />
      <ClubPowerDial />
      <ClubFeed />
    </div>
  );
}
