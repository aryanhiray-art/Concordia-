"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Onboarding } from "@/components/Onboarding";
import { CinematicOverview } from "@/components/CinematicOverview";
import { ClubPerformance } from "@/components/ClubPerformance";
import { LoadingScreen } from "@/components/ui";
import { WIZARD_DONE_KEY, WIZARD_SKIP_KEY, getWizardFlag, setWizardFlag } from "@/components/wizard/flags";

export default function Overview() {
  const { member } = useAuth();
  const router = useRouter();
  const connected = !!(member?.robinhood?.status === "connected" && member?.robinhood?.account_number);
  // "checking" while we decide whether this member belongs in the /welcome
  // wizard instead; "app" renders the overview as before.
  const [gate, setGate] = useState<"checking" | "app">("checking");

  useEffect(() => {
    if (!member) return;
    // Members who skipped the wizard (or were already verified fully set up)
    // go straight to the app — every wizard exit sets the skip flag first, so
    // /welcome and this redirect can never loop.
    if (getWizardFlag(WIZARD_SKIP_KEY) || getWizardFlag(WIZARD_DONE_KEY)) { setGate("app"); return; }
    if (!connected) { router.replace("/welcome"); return; }
    // Decide from the member payload we ALREADY have — never block the page on
    // a network call. The old version awaited /api/me/account, which does live
    // Robinhood reads (measured 4.4s: allocation 1.6s + club P&L 2.3s), so every
    // first visit on a device stared at a blank screen for ~5s. Host-scoped
    // localStorage made that bite again the moment we moved domains.
    // opted_in + a non-zero stake is the same signal minus the round-trip; the
    // resume banner below does the precise check and surfaces anything missing.
    const optedIn = Number(member.settings?.opted_in) === 1;
    const staked = Number(member.settings?.alloc_value ?? 0) > 0;
    if (optedIn && staked) { setWizardFlag(WIZARD_DONE_KEY); setGate("app"); return; }
    router.replace("/welcome");
  }, [member, connected, router]);

  if (gate !== "app") return <LoadingScreen label="loading" />;

  // No brokerage connected ⇒ no trades of your own to plot. Rather than an
  // empty page (or the old invented curve), show what the CLUB has actually
  // done — the one real thing there is to show at this stage.
  if (!connected) {
    return (
      <div className="stagger space-y-5 pt-2">
        <Onboarding />
        <ClubPerformance />
      </div>
    );
  }
  // Connected: Onboarding is now a slim resume banner that self-hides once
  // every step is done. z-10 keeps it above (and clickable over) the
  // full-bleed stage, whose -88px top margin tucks under whatever sits above
  // it; the wrapper is zero-height when Onboarding renders nothing, so the
  // stage still fills the viewport for finished members.
  return (
    <>
      <div className="relative z-10"><Onboarding /></div>
      <CinematicOverview />
    </>
  );
}
