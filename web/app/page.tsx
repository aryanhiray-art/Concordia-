"use client";

import { useAuth } from "@/lib/auth";
import { Onboarding } from "@/components/Onboarding";
import { CinematicOverview } from "@/components/CinematicOverview";

export default function Overview() {
  const { member } = useAuth();
  const connected = !!(member?.robinhood?.status === "connected" && member?.robinhood?.account_number);

  if (!connected) return <div className="stagger space-y-5 pt-2"><Onboarding /></div>;
  return <CinematicOverview />;
}
