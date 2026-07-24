"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Onboarding } from "@/components/Onboarding";
import { CinematicOverview } from "@/components/CinematicOverview";
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
    // Connected but maybe mid-setup: one async look at readiness + stake +
    // participation. Fully done → remember it (DONE flag) so this check never
    // runs again on this device; otherwise resume the wizard at the right step.
    let alive = true;
    (async () => {
      try {
        const [acc, ready] = await Promise.all([api.account(), api.tradeReadiness()]);
        const allDone =
          ready.ready === true &&
          (acc.allocation?.resolved_usd ?? 0) > 0 &&
          Number(member.settings?.opted_in) === 1;
        if (!alive) return;
        if (allDone) { setWizardFlag(WIZARD_DONE_KEY); setGate("app"); }
        else router.replace("/welcome");
      } catch {
        if (alive) setGate("app"); // backend blip — never lock members out of the app
      }
    })();
    return () => { alive = false; };
  }, [member, connected, router]);

  if (gate !== "app") return <LoadingScreen label="loading" />;

  if (!connected) return <div className="stagger space-y-5 pt-2"><Onboarding /></div>;
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
