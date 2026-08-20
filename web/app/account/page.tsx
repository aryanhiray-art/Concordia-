"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { ConnectCard } from "@/components/ConnectCard";
import { AllocationCard } from "@/components/AllocationCard";
import { MemberKeys } from "@/components/MemberKeys";
import { Profile } from "@/components/Profile";
import { DeleteAccount } from "@/components/DeleteAccount";
import { SetupResumeBar, useSetupProgress } from "@/components/Onboarding";

export default function AccountPage() {
  const { member, refreshMember, setMember } = useAuth();
  // API keys live HERE now (owner call: account settings, not the vote page) —
  // same one-click disclosure so the card doesn't crowd the page by default.
  const [showKeys, setShowKeys] = useState(false);
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
      {showKeys ? <MemberKeys /> : (
        <button onClick={() => setShowKeys(true)}
          className="flex w-full items-center gap-2 rounded-[14px] border border-hair bg-white/[0.03] px-4 py-3 text-left text-[12.5px] text-muted transition hover:border-teal/30 hover:text-ink">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-none text-teal">
            <circle cx="8" cy="15" r="4" /><path d="m10.8 12.2 6.2-6.2M15 8l2 2M18 5l2 2" />
          </svg>
          API keys — vote from your own bot
          <span className="ml-auto flex-none text-subtle">+</span>
        </button>
      )}
      <DeleteAccount />
    </div>
  );
}
