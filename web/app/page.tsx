"use client";

import { useAuth } from "@/lib/auth";
import { Onboarding } from "@/components/Onboarding";
import { CinematicOverview } from "@/components/CinematicOverview";

export default function Overview() {
  const { member } = useAuth();
  const connected = !!(member?.robinhood?.status === "connected" && member?.robinhood?.account_number);

  if (!connected) return <div className="stagger space-y-5 pt-2"><Onboarding /></div>;
  // Connected but maybe not fully set up: Onboarding self-hides once every step
  // is done, so always render it above the overview. z-10 keeps the card above
  // (and clickable over) the full-bleed stage, whose -88px top margin tucks
  // under whatever sits above it; the wrapper is zero-height when Onboarding
  // renders nothing, so the stage still fills the viewport for finished members.
  return (
    <>
      <div className="relative z-10"><Onboarding /></div>
      <CinematicOverview />
    </>
  );
}
