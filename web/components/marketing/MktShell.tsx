"use client";

// The chrome every marketing page shares — ticker, nav, footer, the dark
// surface treatment, and the structured-data block. Extracted from
// MarketingSite when the site went from one page to five (2026-09-09): each
// page owns only its <main> content, and everything that must be identical
// across pages lives here exactly once.

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { isAdminHost, isBetaHost, isDemoHost } from "@/lib/host";
import { TopTicker } from "./TopTicker";
import { MktNav } from "./MktNav";
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

export function MktShell({ children }: { children: React.ReactNode }) {
  useMarketingSurface();
  const router = useRouter();

  // The sibling hosts have no marketing surface — demo.*/beta.* ARE the pitch
  // and admin.* is an ops console — so a marketing path there bounces to that
  // host's own front door. A client effect, NOT a render fork: the host
  // helpers are false during SSR, and a render fork on them would hydrate a
  // different tree than the server sent (AppShell renders these pages above
  // its auth gate for exactly that reason).
  useEffect(() => {
    if (isAdminHost() || isDemoHost() || isBetaHost()) router.replace("/");
  }, [router]);

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

      <main id="main">{children}</main>

      <MktFooter />
    </div>
  );
}
