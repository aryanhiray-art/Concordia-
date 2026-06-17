"use client";

import { useAuth } from "@/lib/auth";
import { Onboarding } from "@/components/Onboarding";
import { CinematicOverview } from "@/components/CinematicOverview";

export default function Overview() {
  const { member } = useAuth();
  const connected = !!(member?.robinhood?.status === "connected" && member?.robinhood?.account_number);

  return (
    <>
      <div className={connected ? "" : "pt-2"}>
        <Onboarding />
      </div>
      {connected && <CinematicOverview />}
    </>
  );
}
