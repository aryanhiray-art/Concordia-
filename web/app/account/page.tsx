"use client";

import { useAuth } from "@/lib/auth";
import { ConnectCard } from "@/components/ConnectCard";
import { AllocationCard } from "@/components/AllocationCard";
import { Profile } from "@/components/Profile";
import { DeleteAccount } from "@/components/DeleteAccount";
import { SetupResumeBar, useSetupProgress } from "@/components/Onboarding";

export default function AccountPage() {
  const { member, refreshMember, setMember } = useAuth();
  // Same accounting as the Overview's resume bar (5 wizard steps).
  const { loaded, done, complete } = useSetupProgress();
  if (!member) return null;

  // Gate the cards on CONNECTION, never on setup completion: opting out makes
  // setup "incomplete", and hiding the participation toggle behind completion
  // locked sitting-out members out of turning it back on (the vote page sends
  // them here — a dead loop). While setup is unfinished the resume bar renders
  // ADDITIVELY above the cards; a member with no Robinhood link at all still
  // gets only the bar (the wizard owns first-time connect).
  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">Account</h1>
      </header>

      {loaded && !complete && <SetupResumeBar done={done} />}
      {/* name · email · password, one panel, one idiom */}
      <Profile member={member} onMember={setMember} />
      {member.robinhood && <ConnectCard member={member} onChange={refreshMember} />}
      {member.robinhood?.status === "connected" && <AllocationCard onChange={refreshMember} />}
      <DeleteAccount />
    </div>
  );
}
