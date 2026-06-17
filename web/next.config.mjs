/** @type {import('next').NextConfig} */
// The browser only ever talks to this Vercel app (HTTPS). We proxy /api and the
// Robinhood OAuth callback to the forge backend server-side — no CORS, no
// mixed-content, and the OAuth redirect_uri is this app's own HTTPS URL.
// Defaults to the public forge backend so a Vercel deploy works with no env
// config. Override FORGE_BACKEND for local dev (http://localhost:8780).
const FORGE = (process.env.FORGE_BACKEND || "https://capi.5.78.193.177.sslip.io").replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${FORGE}/api/:path*` },
      { source: "/oauth/:path*", destination: `${FORGE}/oauth/:path*` },
    ];
  },
};
export default nextConfig;
