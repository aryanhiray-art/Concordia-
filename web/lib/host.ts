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
