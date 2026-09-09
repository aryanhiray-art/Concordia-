"use client";

// The public front door at voteconcordia.com — the HOME page of the five-page
// marketing site (split 2026-09-09; the other four live under
// components/marketing/pages/ behind real routes). AppShell hands control here
// for a signed-out visitor at "/"; sign-in lives at /join.
//
// Home's argument, in order: the thesis (Hero), the numbers (ProofStrip), the
// wisdom engine (Engine — the main point of the site, per the owner), the
// mechanic made visible (ProductPreview), doors to the rest (Explore), the
// ask (ClosingCTA). Chrome and page-level side effects live in MktShell.

import { MktShell } from "./MktShell";
import { Hero } from "./Hero";
import { ProofStrip } from "./ProofStrip";
import { Engine } from "./Engine";
import { ProductPreview } from "./ProductPreview";
import { Explore } from "./Explore";
import { ClosingCTA } from "./ClosingCTA";

export function MarketingSite() {
  return (
    <MktShell>
      <Hero />
      <ProofStrip />
      <Engine />
      <ProductPreview />
      <Explore />
      <ClosingCTA />
    </MktShell>
  );
}
