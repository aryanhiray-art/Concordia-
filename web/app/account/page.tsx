"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Member } from "@/lib/types";
import { ConnectCard } from "@/components/ConnectCard";
import { AllocationCard } from "@/components/AllocationCard";
import { ChangeEmail } from "@/components/ChangeEmail";
import { ChangePassword } from "@/components/ChangePassword";
import { DeleteAccount } from "@/components/DeleteAccount";
import { LogoutEverywhere } from "@/components/LogoutEverywhere";
import { SetupResumeBar, useSetupProgress } from "@/components/Onboarding";

// One quiet row under the page title: the display name with a pencil that
// swaps it for an inline input. Enter saves (PUT /api/me/display-name, whose
// response carries the fresh member — applied straight into the auth context),
// Esc cancels, errors land inline. Deliberately not a Card.
function NameRow({ member, onSaved }: { member: Member; onSaved: (m: Member) => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const start = () => { setVal(member.display_name); setErr(null); setEditing(true); };
  const cancel = () => { setEditing(false); setErr(null); };
  async function save() {
    const name = val.trim();
    if (!name || name === member.display_name) { cancel(); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.renameDisplayName(name);
      onSaved(r.member);
      setEditing(false);
    } catch (e: any) {
      setErr(e?.message || "couldn't save your name");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] text-muted">
      {editing ? (
        <>
          <input autoFocus value={val} maxLength={40} disabled={busy} aria-label="Display name"
            onChange={(e) => { setVal(e.target.value); setErr(null); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); save(); }
              else if (e.key === "Escape") cancel();
            }}
            className="w-[190px] rounded-[8px] border border-teal/40 bg-white/[0.05] px-2 py-1 text-[13px] text-ink outline-none focus:border-teal/70 disabled:opacity-50" />
          <span className="text-[11px] text-subtle">{busy ? "saving…" : "Enter to save · Esc to cancel"}</span>
        </>
      ) : (
        <>
          <span>{member.display_name}</span>
          <button onClick={start} aria-label="Edit display name" title="Edit display name"
            className="grid h-6 w-6 place-items-center rounded-[7px] text-subtle transition hover:bg-white/[0.06] hover:text-teal">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
          </button>
        </>
      )}
      {err && <span className="text-[12px] text-red">⚠ {err}</span>}
    </div>
  );
}

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
        <NameRow member={member} onSaved={setMember} />
        <ChangeEmail member={member} onChanged={setMember} />
      </header>

      {loaded && !complete && <SetupResumeBar done={done} />}
      {member.robinhood && <ConnectCard member={member} onChange={refreshMember} />}
      {member.robinhood?.status === "connected" && <AllocationCard onChange={refreshMember} />}
      <ChangePassword />
      <LogoutEverywhere />
      <DeleteAccount />
    </div>
  );
}
