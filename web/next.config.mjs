/** @type {import('next').NextConfig} */
// The browser only ever talks to this app (HTTPS). We proxy /api and the
// Robinhood OAuth callback to the forge backend server-side — no CORS, no
// mixed-content, and the OAuth redirect_uri is this app's own HTTPS URL.
// The backend is NOT public: the web app and backend run on the same host and
// talk over localhost. FORGE_BACKEND is set in the systemd unit to
// http://127.0.0.1:8780; this default is only for local dev.
const FORGE = (process.env.FORGE_BACKEND || "http://127.0.0.1:8780").replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${FORGE}/api/:path*` },
      { source: "/oauth/:path*", destination: `${FORGE}/oauth/:path*` },
      // DEMO_MODE's pretend-Robinhood approval page is served BY THE BACKEND at
      // /demo/robinhood, and rh_oauth.start hands the member that URL. Without
      // this rewrite the browser asks Next for it, gets a 404, and the connect
      // step dead-ends with no URL to copy — the demo could not be completed at
      // all. The backend 404s this path unless demo_mode is on, so proxying it
      // is inert in production.
      { source: "/demo/:path*", destination: `${FORGE}/demo/:path*` },
    ];
  },
  // ── document caching ──────────────────────────────────────────────────────
  // Next serves statically-optimized pages with `s-maxage=31536000,
  // stale-while-revalidate`. `s-maxage` binds SHARED caches only, and with no
  // `max-age` a browser has no explicit freshness lifetime — Safari (iOS
  // especially) then applies heuristic caching and holds the document for
  // days. Because that document names the content-HASHED css/js bundles, a
  // phone keeps requesting last week's asset hashes and never sees a deploy:
  // the owner hit this twice ("i dont see the updated admin ui", then the
  // mobile tint fix). Documents must always revalidate; the hashed assets
  // under /_next/static keep their immutable one-year cache (excluded below),
  // so this costs one conditional request per navigation, not re-downloads.
  async headers() {
    return [
      {
        source: "/((?!_next/static|_next/image|icon.svg|favicon.ico).*)",
        headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
      },
    ];
  },
};
export default nextConfig;
