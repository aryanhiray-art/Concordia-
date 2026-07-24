// localStorage flags shared by the /welcome wizard and the Overview gate.
//
// SKIP — set by every wizard exit ("Skip for now" AND "Enter the club"), so the
//        Overview can never bounce a member straight back into /welcome (no
//        redirect loops). Skipped-but-incomplete members get the slim resume
//        banner (components/Onboarding.tsx) instead.
// DONE — set once the Overview has verified a member is fully set up; lets
//        later visits skip the (async) readiness + allocation check entirely.

export const WIZARD_SKIP_KEY = "concordia_wizard_skipped";
export const WIZARD_DONE_KEY = "concordia_wizard_done";

export function getWizardFlag(key: string): boolean {
  try {
    return typeof window !== "undefined" && window.localStorage.getItem(key) === "1";
  } catch {
    return false; // storage blocked (private mode) — behave as unset
  }
}

export function setWizardFlag(key: string) {
  try {
    window.localStorage.setItem(key, "1");
  } catch {}
}
