import type { MetadataRoute } from "next";
import { headers } from "next/headers";

import { SITE_URL } from "@/components/marketing/seo";

// robots.txt, resolved PER HOST.
//
// One Next build answers on four hostnames (see lib/host.ts): the public club
// at voteconcordia.com, the ops console at admin., the staging channel at
// beta., and the fake-money playground at demo. Only the first may be indexed
// — a demo instance ranking for "concordia investing club" would put fake
// money and fake performance in front of real people.
//
// Next statically generates a metadata route by default, which would bake ONE
// robots.txt into the build and serve it identically on all four hosts. The
// named exports of this file are re-exported onto the generated Route Handler,
// so `dynamic` below is honoured: the route is rendered per request and
// headers() can see which host was asked.
//
// This costs one trivial render per robots.txt fetch, which is a rounding
// error next to the alternative (a hand-written Route Handler per host, or
// four builds).
export const dynamic = "force-dynamic";

/** Hosts allowed to advertise themselves to crawlers. */
function isPublicMarketingHost(hostHeader: string | null): boolean {
  if (!hostHeader) return false;
  const name = hostHeader.split(":")[0].trim().toLowerCase(); // drop :3000
  if (name === "voteconcordia.com" || name === "www.voteconcordia.com") return true;
  // Bare localhost only, so `npm run dev` shows the same file production
  // serves. The dev subdomains (admin./beta./demo.localhost) deliberately fall
  // through to the disallow-everything branch, exactly as they do in prod.
  return name === "localhost" || name === "127.0.0.1";
}

export default function robots(): MetadataRoute.Robots {
  // `Host` FIRST, x-forwarded-host only as a fallback.
  //
  // nginx (infra/nginx-concord-web.conf) sets `proxy_set_header Host $host`
  // and does NOT set X-Forwarded-Host — so on this deployment Host is written
  // by the proxy (and validated by its `server_name` match) while
  // X-Forwarded-Host is whatever the client typed and nginx passed through
  // untouched. Reading the forwarded header first let anyone turn the apex's
  // robots.txt into `Disallow: /`:
  //
  //   curl -H 'X-Forwarded-Host: nope.example' https://voteconcordia.com/robots.txt
  //
  // This order is fail-closed in both directions: an unrecognised Host is
  // never upgraded to "public" by a forwarded header (so beta./demo. cannot be
  // spoofed open either), and the forwarded header is consulted only when
  // there is no Host at all — the case a future CDN in front of nginx would
  // create. If one ever is put in front, teach nginx to set X-Forwarded-Host
  // itself rather than widening this.
  const h = headers();
  const host = h.get("host") ?? h.get("x-forwarded-host");

  // FAIL CLOSED. The expensive failure here is a staging host getting indexed,
  // not the apex being crawled a day late — so anything we do not positively
  // recognise (an unknown host, a missing Host header, a new subdomain nobody
  // remembered to add) is served the disallow-everything file.
  if (!isPublicMarketingHost(host)) {
    // NB: this asks crawlers not to FETCH; it is not a noindex, and a URL
    // linked from elsewhere can still surface. The belt to this pair of braces
    // is the canonical tag in components/marketing/seo.ts, which points every
    // host at production.
    return { rules: { userAgent: "*", disallow: "/" } };
  }

  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // The member app. None of it renders anything useful to a signed-out
      // crawler — it is a client-rendered shell behind an auth gate — and
      // crawling it only burns budget that belongs to the marketing page.
      // Prefixes without a trailing slash, so /stocks and /stocks/AAPL both
      // match.
      disallow: [
        "/vote",
        "/account",
        "/history",
        "/stocks",
        "/welcome",
        "/connected",
        "/join",
        // Proxies to the FastAPI backend (see next.config.mjs rewrites).
        // /oauth in particular carries broker redirect URLs.
        "/api/",
        "/oauth/",
        "/demo/",
      ],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
