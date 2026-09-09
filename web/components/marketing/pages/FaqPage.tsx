"use client";

// /faq — the sceptic's page. Faq's own two-column layout (sticky head left,
// answers right) IS the page design, so there is no PageHero above it — the
// section head is promoted to the h1 instead (Faq passes as="h1" since the
// split; see SectionHead's `as` prop).

import { MktShell } from "../MktShell";
import { Faq } from "../Faq";
import { ClosingCTA } from "../ClosingCTA";

export function FaqPage() {
  return (
    <MktShell>
      <Faq />
      <ClosingCTA />
    </MktShell>
  );
}
