"use client";

import { useAuth } from "@/lib/auth";
import { ConnectCard } from "@/components/ConnectCard";
import { AllocationCard } from "@/components/AllocationCard";
import { ChangePassword } from "@/components/ChangePassword";

export default function AccountPage() {
  const { member, refreshMember } = useAuth();
  if (!member) return null;

  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">Account</h1>
        <div className="mt-2 text-[13px] text-muted">{member.display_name} · {member.email}</div>
      </header>

      <ConnectCard member={member} onChange={refreshMember} />
      {member.robinhood?.status === "connected" && <AllocationCard onChange={refreshMember} />}
      <ChangePassword />
    </div>
  );
}
