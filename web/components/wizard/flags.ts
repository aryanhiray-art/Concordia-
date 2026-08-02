// localStorage flags shared by the /welcome wizard and the app shell's
// onboarding redirect.
//
// SKIP — set by every wizard exit ("Skip for now" AND "Enter the club"), so the
//        app can never bounce a member straight back into /welcome (no redirect
//        loops). Skipped-but-incomplete members get the slim resume banner
//        (components/Onboarding.tsx) instead.
// DONE — set once the Overview has verified a member is fully set up; lets
//        later visits skip the (async) readiness + allocation check entirely.
//
// SCOPED PER MEMBER. These were single browser-global keys, so a NEW account
// inherited the previous account's "skipped" state on the same machine — the
// owner Google-signed-up a fresh demo account and landed in the app with no
// onboarding at all, because an older account in that browser had skipped once.

export const WIZARD_SKIP_KEY = "concordia_wizard_skipped";
export const WIZARD_DONE_KEY = "concordia_wizard_done";

function scoped(key: string, memberId: number | null | undefined): string {
  return memberId ? `${key}:${memberId}` : key;
}

export function getWizardFlag(key: string, memberId?: number | null): boolean {
  try {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(scoped(key, memberId)) === "1";
  } catch {
    return false; // storage blocked (private mode) — behave as unset
  }
}

export function setWizardFlag(key: string, memberId?: number | null) {
  try {
    window.localStorage.setItem(scoped(key, memberId), "1");
  } catch {}
}
