"use client";

// /how-it-works — the mechanism page. The wisdom engine leads (that is the
// product, per the owner's 2026-09-09 reframing); the loop follows; Inside
// backs it with the machinery that makes a shared decision safe to automate.
// This page's single custody line is HowItWorks step 03's existing sentence —
// nothing else here restates it (SPEC §4).

import { MktShell } from "../MktShell";
import { PageHero } from "../PageHero";
import { HowItWorks } from "../HowItWorks";
import { Inside } from "../Inside";
import { ClosingCTA } from "../ClosingCTA";

export function HowItWorksPage() {
  return (
    <MktShell>
      <PageHero
        eyebrow="How it works"
        title="Your vote is weighed, not just counted."
        lead="Bring your own Robinhood account, cast one ballot per cycle across the S&P 500, and watch the tally settle into one basket. The more often you're right, the more your ballot moves it."
        cta={{
          label: "Join with an invite",
          href: "/join",
          secondary: { label: "See the live demo", href: "https://demo.voteconcordia.com" },
        }}
      />
      <HowItWorks />
      <Inside />
      <ClosingCTA />
    </MktShell>
  );
}
