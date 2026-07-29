"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";
import { ConfirmEmail } from "@/components/ConfirmEmail";
import { Nav } from "@/components/Nav";
import { AdminConsole, AdminDenied } from "@/components/AdminConsole";
import { LoadingScreen } from "@/components/ui";
import { isAdminHost } from "@/lib/host";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, member, refreshMember, offline, accountReady } = useAuth();
  const pathname = usePathname();
  // /terms + /privacy are documents, not app screens: they must render for a
  // half-verified member, on the admin host, and without any account data.
  const isLegal = pathname === "/terms" || pathname === "/privacy";

  if (!ready) return <LoadingScreen label="starting concordia" />;
  if (!member) return <AuthGate onAuthed={() => refreshMember()} />;

  // admin.voteconcordia.com: same accounts, entirely different shell — no Nav,
  // no wizard/overview, no member banners. Ops console for owner/admin, a clean
  // "no access" card for everyone else. Normal hosts fall through untouched.
  // The legal pages stay legal pages even here (ConfirmEmail links to /terms;
  // it must not render the console instead of the agreement).
  if (isAdminHost() && !isLegal) {
    if (member.needs_email_verification) return <ConfirmEmail />;
    const isAdmin = member.role === "owner" || member.role === "admin";
    return isAdmin ? <AdminConsole /> : <AdminDenied />;
  }

  // Legacy members must confirm their email before ANYTHING else (wizard
  // included) — the legal pages stay reachable so the doc links keep working.
  if (member.needs_email_verification && !isLegal) {
    return <ConfirmEmail />;
  }

  // /welcome is a standalone, full-screen experience — no Nav, no banners, no
  // footer (owner feedback: "onboarding should be completely separate"). The
  // wizard brings its own logo header and escape hatches, and members who skip
  // still get the Account-page connect fallback. The reauth/offline banners
  // come right back on every other page below.
  if (pathname === "/welcome") {
    return (
      <main className="mx-auto w-[min(1080px,100%)] px-4 pb-24 pt-10 sm:px-6 sm:pt-14">
        {children}
      </main>
    );
  }

  // Account info is checked ONCE, here, before the app proper renders — every
  // screen below then reads the snapshot out of the auth context instead of
  // re-paying a broker read on each navigation. accountReady flips on the first
  // SETTLED attempt (success OR failure), so a broker/API blip delays the shell
  // by one request, never blocks it. Deliberately placed after the four gates
  // above: the admin console, ConfirmEmail, the legal docs and the /welcome
  // wizard all return before this line and never wait on account data — the
  // wizard especially, since a member who hasn't connected Robinhood yet has no
  // snapshot to wait for.
  if (!accountReady && !isLegal) return <LoadingScreen label="loading your account" />;

  // Robinhood rotated the tokens out from under us — trading is paused until reconnect.
  const needsReauth = member?.robinhood?.status === "error";
  const hasBanner = offline || needsReauth;

  return (
    <>
      <Nav />
      {hasBanner && (
        <div className="mx-auto flex w-[min(1080px,100%)] flex-col gap-2 px-4 pt-[88px] sm:px-6">
          {/* backend blip — auth.tsx is retrying in the background; wins visually over reauth */}
          {offline && (
            <div className="flex items-center gap-3 rounded-[14px] border border-red/30 bg-red/[0.08] px-4 py-2.5 text-[13px] text-red">
              <span>⚠ Can&apos;t reach Concordia right now — retrying…</span>
            </div>
          )}
          {needsReauth && (
            <div className="flex items-center gap-3 rounded-[14px] border border-amber/30 bg-amber/[0.08] px-4 py-2.5 text-[13px] text-amber">
              <span>⚠ Robinhood needs a quick reconnect — trading for your account is paused until you do.</span>
              <Link href="/account" className="ml-auto whitespace-nowrap font-semibold hover:underline">Reconnect →</Link>
            </div>
          )}
        </div>
      )}
      <main className={`mx-auto w-[min(1080px,100%)] px-4 pb-24 ${hasBanner ? "pt-4" : "pt-[88px]"} sm:px-6`}>
        {children}
      </main>
      <p className="pb-8 text-center text-[11px] text-subtle">
        Concordia · a trading club that votes · one shared basket, rebalanced in your own Robinhood
      </p>
    </>
  );
}
