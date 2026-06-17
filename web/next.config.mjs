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
    ];
  },
};
export default nextConfig;
