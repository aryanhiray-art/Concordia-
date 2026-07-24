"use client";

// The /welcome onboarding wizard — a guided 5-step journey replacing the old
// drop-them-in-the-app checklist. Centered ~640px column, small Logo up top,
// a stepper with the current step lit, Back among completed steps, and a quiet
// escape hatch at the bottom (never trap anyone).
//
// STEP DERIVATION (on load, once): the furthest COMPLETED step decides where a
// returning member resumes —
//   not connected, never was    → 1  (rules first)
//   not connected, was before   → 2  (reconnect — skip the rules re-read)
//   connected, readiness check
//     pending/failed or !ready  → 3  (the check re-runs there anyway)
//   connected + ready, no stake → 4
//   connected + ready + stake   → 5  (participation + recap, even if opted in)
// Back and the stepper move freely among steps ≤ the furthest reached
// (`unlocked`); /welcome never redirects out except via its own CTAs, and both
// exits ("Skip for now" and "Enter the club") set the skip flag first so the
// Overview can never bounce a member back here (no redirect loops).

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Dots, Logo } from "@/components/ui";
import { WIZARD_SKIP_KEY, setWizardFlag } from "./flags";
import { Stepper } from "./Stepper";
import { StepRules } from "./StepRules";
import { StepConnect } from "./StepConnect";
import { StepReadiness } from "./StepReadiness";
import { StepStake } from "./StepStake";
import { StepFinish } from "./StepFinish";

const TITLES = [
  "How the club works",
  "Connect your Robinhood",
  "One quick Robinhood check",
  "Commit your stake",
  "You're in",
];

export function Wizard() {
  const { member } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [unlocked, setUnlocked] = useState(1); // furthest step reached this visit
  const [derived, setDerived] = useState(false);

  useEffect(() => {
    if (derived || !member) return; // derive once — later member refreshes must not yank the member around
    const rh = member.robinhood;
    const connected = rh?.status === "connected" && !!rh?.account_number;
    if (!connected) {
      const start = rh?.account_number ? 2 : 1;
      setStep(start); setUnlocked(start); setDerived(true);
      return;
    }
    let alive = true;
    (async () => {
      let start = 3;
      try {
        const [acc, r] = await Promise.all([api.account(), api.tradeReadiness()]);
        if (r.ready === true) start = (acc.allocation?.resolved_usd ?? 0) > 0 ? 5 : 4;
      } catch {
        // can't tell — resume at the readiness step, which re-checks itself
      }
      if (!alive) return;
      setStep(start); setUnlocked(start); setDerived(true);
    })();
    return () => { alive = false; };
  }, [member, derived]);

  const go = useCallback((n: number) => {
    setStep(n);
    setUnlocked((u) => (n > u ? n : u));
  }, []);
  const next = useCallback(() => go(Math.min(5, step + 1)), [go, step]);
  const back = useCallback(() => setStep((s) => Math.max(1, s - 1)), []);

  // Both exits set the skip flag BEFORE navigating: the Overview redirects
  // not-set-up members to /welcome unless it's set, so this is what makes
  // leaving mid-setup (or finishing) stick instead of bouncing straight back.
  const leaveToApp = useCallback(() => {
    setWizardFlag(WIZARD_SKIP_KEY);
    router.push("/");
  }, [router]);

  if (!member) return null; // AppShell shows the auth gate instead

  return (
    <div className="mx-auto w-full max-w-[640px] pb-10 pt-2">
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
          <div key={step} className="enter mt-6">
            <div className="glass shine p-5 sm:p-8">
              <div className="flex items-baseline justify-between gap-3">
                <span className="label">Step {step} of 5</span>
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
                {step === 2 && <StepConnect onDone={next} />}
                {step === 3 && <StepReadiness onDone={next} auto={unlocked === 3} />}
                {step === 4 && <StepStake onNext={next} />}
                {step === 5 && <StepFinish onEnter={leaveToApp} />}
              </div>
            </div>
          </div>

          <div className="mt-5 text-center">
            <button onClick={leaveToApp} className="text-[12px] text-subtle transition hover:text-muted">
              Skip for now → go to the app
            </button>
          </div>
        </>
      )}
    </div>
  );
}
