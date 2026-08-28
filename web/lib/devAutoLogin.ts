// Local-sandbox convenience: land inside the app instead of on the sign-in card.
//
// WHY THIS CANNOT REACH PRODUCTION
// --------------------------------
// The first guard is `process.env.NODE_ENV !== "production"`. Next inlines that
// literal at build time, so in a `next build` bundle the whole body below is
// unreachable and gets dropped by the minifier — the credentials are not merely
// unused in production, they are not present in the shipped JavaScript. The
// hostname guard is a second, independent lock for the case where somebody runs
// `next dev` against a real backend.
//
// The credentials are for the throwaway DEMO_MODE database that scripts/dev.sh
// creates: fake Robinhood, fake money, its own SQLite file. They are not a
// secret and they open nothing that exists anywhere else.
//
// Bare localhost only: admin./beta./demo. subdomains have their own sign-in and
// role gates, and silently authenticating into the ops console would be a
// genuinely bad surprise.
//
// ESCAPE HATCH: append ?signin to any URL to drop the session and get the real
// sign-in screen back — you need it to test the auth flows themselves. For a
// sticky opt-out, run `localStorage.setItem("concord_dev_no_autologin","1")`.

import { setToken } from "@/lib/api";

/** The member scripts/dev.sh + the seeding note in RUNBOOK create locally. */
const SANDBOX_EMAIL = "uicheck@demo.local";
const SANDBOX_PASSWORD = "demo-local-only-1";

const OPT_OUT_KEY = "concord_dev_no_autologin";

/** True on a local dev sandbox — the precondition for anything in this module. */
function inSandbox(): boolean {
  if (process.env.NODE_ENV === "production") return false;
  if (typeof window === "undefined") return false;
  const host = window.location.hostname;
  return host === "localhost" || host === "127.0.0.1";
}

/** `?signin` asks for the real sign-in screen. It has to CLEAR an existing
 *  session as well as suppress the auto-login, or the flag does nothing once
 *  you have already been signed in automatically — which is exactly when you
 *  reach for it. Call this before the token check. */
export function devWantsSignIn(): boolean {
  if (!inSandbox()) return false;
  return new URLSearchParams(window.location.search).has("signin");
}

function enabled(): boolean {
  if (!inSandbox()) return false;

  if (devWantsSignIn()) return false;
  try {
    if (window.localStorage.getItem(OPT_OUT_KEY) === "1") return false;
  } catch { /* blocked storage: fall through and just try */ }
  return true;
}

/** Signs into the local sandbox if possible. Resolves either way — a missing or
 *  reset demo database must fall through to the normal sign-in card, never
 *  throw and never block the shell from becoming ready. */
export async function devAutoLogin(): Promise<boolean> {
  if (!enabled()) return false;

  try {
    const base = process.env.NEXT_PUBLIC_CONCORD_API || "";
    const res = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: SANDBOX_EMAIL, password: SANDBOX_PASSWORD }),
    });
    if (!res.ok) return false;

    const data = await res.json();
    if (!data?.token) return false;
    setToken(data.token);

    // A member who is already connected and committed should land in the app,
    // not be bounced into /welcome. These are the same per-member flags the
    // wizard sets on its way out.
    const id = data?.member?.id;
    if (id != null) {
      try {
        window.localStorage.setItem(`concord_wizard_done_${id}`, "1");
        window.localStorage.setItem(`concord_wizard_skip_${id}`, "1");
      } catch { /* non-fatal */ }
    }
    return true;
  } catch {
    return false;   // backend not up yet, DB reset, whatever — just show sign-in
  }
}
