"use client";

import { useAuth } from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";
import { Nav } from "@/components/Nav";
import { LoadingScreen } from "@/components/ui";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, member, refreshMember } = useAuth();

  if (!ready) return <LoadingScreen label="starting concordia" />;
  if (!member) return <AuthGate onAuthed={() => refreshMember()} />;

  return (
    <>
      <Nav />
      <main className="mx-auto w-[min(1080px,100%)] px-4 pb-24 pt-[88px] sm:px-6">
        {children}
      </main>
      <p className="pb-8 text-center text-[11px] text-subtle">
        Concordia · a trading club that votes · one ballot a day, executed on your own Robinhood
      </p>
    </>
  );
}
