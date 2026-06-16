"use client";

import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";
import type { Member, SessionView, UniverseItem } from "@/lib/types";
import { AuthGate } from "@/components/AuthGate";
import { ConnectCard } from "@/components/ConnectCard";
import { Ballot } from "@/components/Ballot";
import { Results } from "@/components/Results";
import { AdminStrip } from "@/components/AdminStrip";
import { Button, Logo } from "@/components/ui";

export default function Home() {
  const [ready, setReady] = useState(false);
  const [member, setMember] = useState<Member | null>(null);
  const [universe, setUniverse] = useState<UniverseItem[]>([]);
  const [session, setSession] = useState<SessionView | null>(null);

  const refreshMember = useCallback(async () => {
    try { setMember((await api.me()).member); } catch { setMember(null); setToken(null); }
  }, []);
  const refreshSession = useCallback(async () => {
    try { setSession(await api.voteToday()); } catch {}
  }, []);

  useEffect(() => {
    (async () => {
      if (getToken()) await refreshMember();
      try { setUniverse((await api.universe()).symbols); } catch {}
      setReady(true);
    })();
  }, [refreshMember]);

  useEffect(() => {
    if (!member) return;
    refreshSession();
    const id = setInterval(refreshSession, 5000);
    return () => clearInterval(id);
  }, [member, refreshSession]);

  if (!ready) return <div className="grid min-h-screen place-items-center text-subtle"><Logo size={44} /></div>;
  if (!member) return <AuthGate onAuthed={(m) => setMember(m)} />;

  const open = session?.status === "open";

  return (
    <main className="mx-auto w-[min(760px,100%)] px-4 py-8 sm:py-10">
      <header className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <div>
            <div className="font-display text-[18px] font-bold leading-none tracking-tight">
              con<span className="text-teal">·</span>cord
            </div>
            <div className="mt-1 text-[11.5px] text-subtle">{member.display_name} · {member.email}</div>
          </div>
        </div>
        <Button variant="ghost" onClick={() => { setToken(null); setMember(null); }}>Log out</Button>
      </header>

      <div className="space-y-4">
        {member.role === "owner" && <AdminStrip onAction={refreshSession} />}
        <ConnectCard member={member} onChange={refreshMember} />
        {session && (open
          ? <Ballot universe={universe} session={session} onVoted={setSession} />
          : <Results session={session} />)}
      </div>

      <p className="mt-6 text-center text-[11px] text-subtle">
        Concord · collective daily-vote trading · executes at the open
      </p>
    </main>
  );
}
