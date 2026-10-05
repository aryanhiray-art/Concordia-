"use client";

// The /welcome onboarding wizard — a guided 6-screen journey replacing the old
// drop-them-in-the-app checklist. Centered ~640px column, small Logo up top,
// a stepper with the current step lit, Back among completed steps, and a quiet
// escape hatch at the bottom (never trap anyone). Every screen is deliberately
// short — title + one compact action block — so it fits a laptop viewport
// (~700px) without scrolling.
//
// STEP DERIVATION (on load, once): the furthest COMPLETED step decides where a
// returning member resumes —
//   not connected, never was    → 1  (rules first)
//   not connected, was before   → 2  (reconnect — skip the rules re-read)
//   connected, readiness check
//     pending/failed or !ready  → 4  (the check re-runs there anyway)
//   connected + ready, no stake → 5
//   connected + ready + stake   → 6  (participation + recap, even if opted in)
// Step 3 (paste the URL back) is transient — a half-done OAuth approval can't
// be resumed, so derivation never starts there; step 2 restarts the flow.
// Back and the stepper move freely among steps ≤ the furthest reached
// (`unlocked`); /welcome never redirects out except via its own CTAs, and both
// exits ("Skip for now" and "Enter the club") set the skip flag first so the
// Overview can never bounce a member back here (no redirect loops).

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Dots, Logo } from "@/components/ui";
import { WIZARD_SKIP_KEY, setWizardFlag } from "./flags";
import { Stepper } from "./Stepper";
import { StepRules } from "./StepRules";
import { StepConnect } from "./StepConnect";
import { StepPaste } from "./StepPaste";
import { StepReadiness } from "./StepReadiness";
import { StepStake } from "./StepStake";
import { StepFinish } from "./StepFinish";

const LAST = 6;

const TITLES = [
  "How the club works",
  "Connect your Robinhood",
  "Paste the URL back",
  "One quick check",
  "Commit your stake",
  "You're in",
];

export function Wizard() {
  const { member, account, accountReady } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [unlocked, setUnlocked] = useState(1); // furthest step reached this visit
  const [derived, setDerived] = useState(false);
  // null = not read yet; true/false = the latched answer (see the effect below)
  const inflightRef = useRef<boolean | null>(null);

  useEffect(() => {
    if (derived || !member) return; // derive once — later member refreshes must not yank the member around
    const rh = member.robinhood;
    const connected = rh?.status === "connected" && !!rh?.account_number;
    if (!connected) {
      // A blocked popup navigates THIS tab to the approval page; when the
      // member comes back mid-OAuth, re-deriving to step 1 hides the paste
      // step's instructions exactly when they are holding the URL to paste.
      //
      // LATCH THE FLAG IN A REF. Reading it was destructive while this effect
      // can run more than once before `derived` commits — the first run ate the
      // key and called setStep(3), the second re-ran with `derived` still false,
      // found nothing, and derived step 1 over the top. StrictMode's double
      // invoke makes that certain in dev (so `./scripts/dev.sh` always lost it),
      // and in production the deps below re-fire as the account snapshot settles
      // right after connecting — exactly when a member is holding the URL.
      // A ref survives both re-runs and StrictMode's remount, so every run after
      // the first reuses the latched answer instead of re-reading a consumed key.
      if (inflightRef.current === null) {
        try {
          inflightRef.current = sessionStorage.getItem("concordia_oauth_inflight") === "1";
          sessionStorage.removeItem("concordia_oauth_inflight");
        } catch { inflightRef.current = false; }
      }
      const inflight = inflightRef.current;
      const start = inflight ? 3 : rh?.account_number ? 2 : 1;
      setStep(start); setUnlocked(start); setDerived(true);
      return;
    }
    // The stake comes from the shared snapshot; wait for its first attempt to
    // settle (success OR failure) so we don't derive step 5 from a null account.
    // /welcome is deliberately NOT gated on accountReady in AppShell — a member
    // who just connected has no snapshot yet, and this is the only step that
    // actually needs one.
    if (!accountReady) return;
    let alive = true;
    (async () => {
      let start = 4;
      try {
        const r = await api.tradeReadiness();
        if (r.ready === true) start = (account?.allocation?.resolved_usd ?? 0) > 0 ? 6 : 5;
      } catch {
        // can't tell — resume at the readiness step, which re-checks itself
      }
      if (!alive) return;
      setStep(start); setUnlocked(start); setDerived(true);
    })();
    return () => { alive = false; };
  }, [member, derived, account, accountReady]);

  const go = useCallback((n: number) => {
    setStep(n);
    setUnlocked((u) => (n > u ? n : u));
  }, []);
  const next = useCallback(() => go(Math.min(LAST, step + 1)), [go, step]);
  const back = useCallback(() => setStep((s) => Math.max(1, s - 1)), []);

  // Both exits set the skip flag BEFORE navigating: the Overview redirects
  // not-set-up members to /welcome unless it's set, so this is what makes
  // leaving mid-setup (or finishing) stick instead of bouncing straight back.
  const leaveToApp = useCallback(() => {
    setWizardFlag(WIZARD_SKIP_KEY, member?.id);
    router.push("/");
  }, [router, member?.id]);

  if (!member) return null; // AppShell shows the auth gate instead

  // pb-10 here stacked on the shell's own bottom padding. A wizard step is a
  // self-contained screen that should fit a laptop viewport without scrolling,
  // so the bottom padding lives in ONE place (AppShell's /welcome branch).
  return (
    <div className="mx-auto w-full max-w-[640px] pb-2 pt-2">
      <div className="flex flex-col items-center gap-4">
        <Logo size={34} />
        <Stepper step={step} unlocked={unlocked} onGo={go} />
      </div>

      {!derived ? (
        <div className="mt-16 flex flex-col items-center gap-3 text-[12px] text-subtle">
          <Dots />
          <span>checking where you left off…</span>
        </div>
      ) : (
        <>
          {/* key={step} replays the entrance on each step change — .enter is a
              subtle rise+fade, already stilled under prefers-reduced-motion */}
          <div key={step} className="enter mt-5">
            <div className="glass shine p-5 sm:p-6">
              <div className="flex items-baseline justify-between gap-3">
                <span className="label">Step {step} of {LAST}</span>
                {step > 1 && (
                  <button onClick={back} className="text-[12px] text-subtle transition hover:text-ink">
                    ← Back
                  </button>
                )}
              </div>
              <h1 className="mt-2 font-display text-[22px] font-bold tracking-tight">
                {TITLES[step - 1]}
              </h1>
              <div className="mt-4">
                {step === 1 && <StepRules onNext={next} />}
                {/* already connected → skip the paste screen entirely */}
                {step === 2 && <StepConnect onOpened={() => go(3)} onConnected={() => go(4)} />}
                {step === 3 && <StepPaste onDone={() => go(4)} />}
                {step === 4 && <StepReadiness onDone={next} auto={unlocked === 4} />}
                {step === 5 && <StepStake onNext={next} />}
                {step === 6 && <StepFinish onEnter={leaveToApp} />}
              </div>
            </div>
          </div>

          <div className="mt-4 text-center">
            <button onClick={leaveToApp} className="text-[12px] text-subtle transition hover:text-muted">
              Skip for now → go to the app
            </button>
          </div>
        </>
      )}
    </div>
  );
}
