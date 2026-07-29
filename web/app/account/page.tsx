"use client";

import { useAuth } from "@/lib/auth";
import { ConnectCard } from "@/components/ConnectCard";
import { AllocationCard } from "@/components/AllocationCard";
import { ChangePassword } from "@/components/ChangePassword";
import { DeleteAccount } from "@/components/DeleteAccount";
import { SetupResumeBar, useSetupProgress } from "@/components/Onboarding";

export default function AccountPage() {
  const { member, refreshMember } = useAuth();
  // Same accounting as the Overview's resume bar (5 wizard steps).
  const { loaded, done, complete } = useSetupProgress();
  if (!member) return null;

  // Robinhood rotated the tokens out from under a member who WAS set up —
  // that's the one case the full ConnectCard (reconnect flow) still owns.
  // Everything else unfinished points back at the wizard instead of inlining
  // the whole connect/onboarding flow here.
  const reconnect = member.robinhood?.status === "error";
  const showFull = reconnect || complete;

  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">Account</h1>
        <div className="mt-2 text-[13px] text-muted">{member.display_name} · {member.email}</div>
      </header>

      {showFull ? (
        <>
          <ConnectCard member={member} onChange={refreshMember} />
          {member.robinhood?.status === "connected" && <AllocationCard onChange={refreshMember} />}
        </>
      ) : (
        loaded && <SetupResumeBar done={done} />
      )}
      <ChangePassword />
      <DeleteAccount />
    </div>
  );
}
