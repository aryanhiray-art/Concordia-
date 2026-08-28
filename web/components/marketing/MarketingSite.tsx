"use client";

// The public front door at voteconcordia.com — everything a signed-out visitor
// sees. AppShell hands control here instead of to <AuthGate/>; sign-in now
// lives at /join.
//
// This file owns only composition and the two page-level side effects; every
// section is its own module so the page can be re-ordered without touching
// anything that renders.

import { useEffect } from "react";

import { TopTicker } from "./TopTicker";
import { MktNav } from "./MktNav";
import { Hero } from "./Hero";
import { ProofStrip } from "./ProofStrip";
import { Why } from "./Why";
import { HowItWorks } from "./HowItWorks";
import { ProductPreview } from "./ProductPreview";
import { Difference } from "./Difference";
import { TrackRecord } from "./TrackRecord";
import { Inside } from "./Inside";
import { Faq } from "./Faq";
import { ClosingCTA } from "./ClosingCTA";
import { MktFooter } from "./MktFooter";
import { jsonLd } from "./seo";

// The marketing canvas is darker than the app's (#050809 vs #06080C). Both the
// body background and <meta theme-color> have to move with it, for reasons the
// member app learned the hard way over four rounds of bug reports:
//
//   * Safari 26 ignores theme-color entirely and samples the BODY element's
//     computed background-color to tint its own chrome. Leave body on the app's
//     colour and the phone draws a visible seam above the page.
//   * Safari 25 and Chrome Android still read the meta tag and ignore the body.
//
// So both are set, to the same value, and both are restored on unmount — a
// member who signs in from /join navigates from this surface into the app
// without a reload, and must not carry the marketing tint in with them.
const MKT_BG = "#050809";

function useMarketingSurface() {
  useEffect(() => {
    const body = document.body;
    const meta = document.querySelector('meta[name="theme-color"]');
    const prevTheme = meta?.getAttribute("content") ?? null;

    body.dataset.surface = "mkt";
    meta?.setAttribute("content", MKT_BG);

    return () => {
      delete body.dataset.surface;
      if (meta && prevTheme !== null) meta.setAttribute("content", prevTheme);
    };
  }, []);
}

export function MarketingSite() {
  useMarketingSurface();

  return (
    <div className="mkt min-h-screen">
      {/* Search engines read the club as an organisation, not a product page.
          Claims here are the same ones on the page — see seo.ts. */}
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded-full focus:bg-[var(--mkt-ink)] focus:px-4 focus:py-2 focus:text-[13px] focus:font-semibold focus:text-[#04100c]"
      >
        Skip to content
      </a>

      <TopTicker />
      <MktNav />

      <main id="main">
        <Hero />
        <ProofStrip />
        <Why />
        <HowItWorks />
        <ProductPreview />
        <Difference />
        <TrackRecord />
        <Inside />
        <Faq />
        <ClosingCTA />
      </main>

      <MktFooter />
    </div>
  );
}
