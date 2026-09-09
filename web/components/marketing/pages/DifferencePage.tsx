"use client";

// /difference — the structural argument. Why carries the predecessor story;
// Difference carries the three-way comparison. This page's single
// benefit-framed custody line is Why's closing statement block ("Nobody
// holding the money.") — the comparison table's "Who holds the money" row is
// structural fact applied to all three columns, not benefit copy, so it
// doesn't count against the budget (SPEC §4).

import { MktShell } from "../MktShell";
import { PageHero } from "../PageHero";
import { Why } from "../Why";
import { Difference } from "../Difference";
import { ClosingCTA } from "../ClosingCTA";

export function DifferencePage() {
  return (
    <MktShell>
      <PageHero
        eyebrow="The difference"
        title="A vote with a memory."
        lead="Every predecessor gave something up — the vote, the account, or both. Concordia keeps the vote and your account, and it keeps score: the members who keep being right carry more of the decision."
        cta={{
          label: "Join with an invite",
          href: "/join",
          secondary: { label: "See the live demo", href: "https://demo.voteconcordia.com" },
        }}
      />
      <Why />
      <Difference />
      <ClosingCTA />
    </MktShell>
  );
}
