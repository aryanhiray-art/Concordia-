// Which host is this app being served from? The same Next build serves both
// voteconcordia.com (member site) and admin.voteconcordia.com (ops console) —
// the admin host swaps the entire shell for a role-gated console, so this
// check must be cheap, SSR-safe (false on the server; the shell's first paint
// is a loading screen on both sides, so there's no hydration mismatch), and
// cover local dev (admin.localhost:3000).
export function isAdminHost(): boolean {
  if (typeof window === "undefined") return false;
  const h = window.location.hostname;
  return h === "admin.voteconcordia.com" || h.startsWith("admin.");
}

/** demo.voteconcordia.com — the public fake-money instance. Branding gains a
 *  ".demo" suffix and Google sign-in hides (GIS origin not authorized there).
 *  Covers demo.localhost dev too. */
export function isDemoHost(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.hostname.startsWith("demo.");
}

/** beta.voteconcordia.com — the real club's staging channel: same code and
 *  real broker flows, but its own database and execution locked to dry-run
 *  on the server side. Branding gains a ".beta" suffix exactly like ".demo";
 *  Google sign-in hides (GIS origin not authorized). Covers beta.localhost. */
export function isBetaHost(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.hostname.startsWith("beta.");
}
