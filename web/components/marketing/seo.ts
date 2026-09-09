// How Concordia describes itself to search engines, social cards and crawlers.
//
// Everything in this file is a PUBLIC CLAIM about a real club that moves real
// money, so it is held to the same bar as the page copy: only facts from
// SPEC.md §2/§3 appear, and nothing here asserts advice, registration or a
// return. Structured data is where sites quietly over-claim — an address they
// do not have, a founding team, a rating — so the JSON-LD below is
// deliberately thin and the omissions are documented at the bottom.
//
// Not a client module by intent, but it must stay importable from one:
// MarketingSite.tsx is "use client" and renders `jsonLd` into a <script>. So
// this file holds plain serialisable values and a type-only Next import —
// no next/headers, no server-only, no React.

import type { Metadata } from "next";

/** Canonical production origin. Deliberately a constant, not an env var: the
 *  same build also answers on beta./demo./admin., and every one of those hosts
 *  must point its canonical at production rather than at itself. */
export const SITE_URL = "https://voteconcordia.com";
export const SITE_NAME = "Concordia";

/** SPEC.md §1's model sentence. Single source of truth — the OG card renders
 *  the same three clauses, so the share image and the <meta> never drift. */
export const TAGLINE_LINES = [
  "The club votes.",
  "Your account trades.",
  "Nobody holds your money.",
] as const;

export const TAGLINE = TAGLINE_LINES.join(" ");

const TITLE = "Concordia — the club votes, your account trades";

// ~150 chars: long enough to carry the three load-bearing facts (vote, own
// account, no custody), short enough that Google shows all of it.
const DESCRIPTION =
  "An invite-only investing club. The club votes on which S&P 500 names to hold. " +
  "The basket trades in your own Robinhood account. Nobody holds your money.";

export const siteMetadata: Metadata = {
  // Absolute-URL base for canonical + og:url + og:image. Without it Next emits
  // relative OG URLs, which several scrapers refuse to resolve.
  metadataBase: new URL(SITE_URL),
  title: TITLE,
  description: DESCRIPTION,
  applicationName: SITE_NAME,
  publisher: SITE_NAME,
  keywords: [
    "investing club",
    "invite-only investing club",
    "vote on stocks",
    "S&P 500 basket",
    "non-custodial investing",
    "trade in your own brokerage account",
    "Robinhood agentic API",
    "collective stock picking",
  ],
  // Every host serving this build (beta., demo., and the apex) declares the
  // production URL as canonical. robots.txt keeps crawlers off the staging
  // hosts; this is the second layer, for the case where one is reached anyway
  // through an external link — the duplicate consolidates onto production
  // instead of competing with it.
  alternates: { canonical: "/" },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    url: SITE_URL,
    title: TITLE,
    description: DESCRIPTION,
    locale: "en_US",
    // `images` is intentionally ABSENT. app/opengraph-image.tsx is a file
    // convention; Next resolves it after this object and file-based metadata
    // wins, adding the route URL with a content hash for cache-busting. Naming
    // the image here would override that with an unhashed URL and pin stale
    // cards in every social scraper's cache.
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
  },
  // The page is dense with figures that look like phone numbers to iOS Safari
  // ("09:15 ET", "+6.85%", "2026-06-17"); auto-linking any of them would put a
  // blue tel: link in the middle of a disclosure.
  formatDetection: { telephone: false },
};

/* ── Structured data ───────────────────────────────────────────────────────
   Organization + WebSite, rendered by MarketingSite.tsx into one
   application/ld+json script.

   `Organization` and not `FinancialService`/`InvestmentOrDeposit`: the latter
   describe a regulated provider, which Concordia is not and must never imply
   (SPEC.md §0). Everything asserted below is also visible on the page — a
   claim that exists only in structured data is a claim nobody proofreads.

   Deliberately NOT present, because the club cannot evidence them:
   address / telephone / email, founder or foundingDate, employee counts,
   aggregateRating or review, sameAs profiles, offers or price, and
   WebSite.potentialAction (there is no site search to point it at). */
export const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_URL}/#organization`,
      name: SITE_NAME,
      url: SITE_URL,
      description:
        "An invite-only investing club. Members vote each cycle on which S&P 500 " +
        "names the club should hold, and the winning basket executes in each " +
        "member's own Robinhood account. Concordia never holds member money.",
      slogan: TAGLINE,
      logo: {
        "@type": "ImageObject",
        url: `${SITE_URL}/icon.svg`,
        width: 100,
        height: 100,
      },
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_URL}/#website`,
      name: SITE_NAME,
      url: SITE_URL,
      description: DESCRIPTION,
      inLanguage: "en-US",
      publisher: { "@id": `${SITE_URL}/#organization` },
    },
  ],
};
