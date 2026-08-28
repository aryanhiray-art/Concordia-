import type { MetadataRoute } from "next";

import { SITE_URL } from "@/components/marketing/seo";

// The public marketing surface, and nothing else. Every other route in this
// app (/vote, /account, /history, /stocks, /welcome, /connected, /join) is
// behind the auth gate; listing one would advertise a members-only URL and
// hand crawlers a page that renders as an empty shell. robots.txt disallows
// them too — a sitemap that contradicts robots.txt is a ranking problem, not
// just an untidy one.
//
// Absolute production URLs, always. Unlike robots.txt this file does not vary
// by host: it is only ever advertised from the apex's robots.txt, and if a
// crawler finds it on beta./demo. the correct answer is still the production
// URL set.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${SITE_URL}/`,
      // The homepage's live figures move with the club's weekly cadence
      // (ballot Thursday → basket Monday), so weekly is the honest signal.
      changeFrequency: "weekly",
      priority: 1,
    },
    { url: `${SITE_URL}/terms`, changeFrequency: "yearly", priority: 0.3 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ];
  // No `lastModified`. The obvious `new Date()` resolves at build time, which
  // would claim all three pages changed on every deploy — including deploys
  // that touched neither. Google discounts a lastmod it catches lying, so an
  // absent one is worth more than a fabricated one.
}
