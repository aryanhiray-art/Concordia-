"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";
import { Nav } from "@/components/Nav";
import { LoadingScreen } from "@/components/ui";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, member, refreshMember } = useAuth();

  if (!ready) return <LoadingScreen label="starting concordia" />;
  if (!member) return <AuthGate onAuthed={() => refreshMember()} />;

  // Robinhood rotated the tokens out from under us — trading is paused until reconnect.
  const needsReauth = member?.robinhood?.status === "error";

  return (
    <>
      <Nav />
      {needsReauth && (
        <div className="mx-auto w-[min(1080px,100%)] px-4 pt-[88px] sm:px-6">
          <div className="flex items-center gap-3 rounded-[14px] border border-amber/30 bg-amber/[0.08] px-4 py-2.5 text-[13px] text-amber">
            <span>⚠ Robinhood needs a quick reconnect — trading for your account is paused until you do.</span>
            <Link href="/account" className="ml-auto whitespace-nowrap font-semibold hover:underline">Reconnect →</Link>
          </div>
        </div>
      )}
      <main className={`mx-auto w-[min(1080px,100%)] px-4 pb-24 ${needsReauth ? "pt-4" : "pt-[88px]"} sm:px-6`}>
        {children}
      </main>
      <p className="pb-8 text-center text-[11px] text-subtle">
        Concordia · a trading club that votes · one ballot a day, executed on your own Robinhood
      </p>
    </>
  );
}
