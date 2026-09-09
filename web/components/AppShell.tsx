"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";
import { ConfirmEmail } from "@/components/ConfirmEmail";
import { Nav } from "@/components/Nav";
import { AdminConsole, AdminDenied } from "@/components/AdminConsole";
import { LoadingScreen } from "@/components/ui";
import { MarketingSite } from "@/components/marketing/MarketingSite";
import { MKT_PAGE_PATHS } from "@/components/marketing/seo";
import { isAdminHost, isBetaHost, isDemoHost } from "@/lib/host";
import { WIZARD_DONE_KEY, WIZARD_SKIP_KEY, getWizardFlag } from "@/components/wizard/flags";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, member, refreshMember, offline, accountReady, signedOutNote } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  // /terms + /privacy are documents, not app screens: they must render for a
  // half-verified member, on the admin host, and without any account data.
  const isLegal = pathname === "/terms" || pathname === "/privacy";
  // The marketing subpages are public documents too (the site split into five
  // pages, 2026-09-09): they bring their own chrome (MktShell) and must render
  // for anyone — signed out, signed in, half-verified — without the app shell
  // wrapping them and without the welcome-wizard redirect yanking a member off
  // a page that is just prose.
  const isMktPage = MKT_PAGE_PATHS.includes(pathname);

  // A brand-new member belongs in /welcome no matter WHERE they signed in.
  // The redirect used to live only on the Overview route, so someone who
  // authenticated while sitting on /account (an idle-logged-out tab, a
  // bookmark) landed straight in the app with no onboarding — the owner hit
  // exactly this with a fresh Google signup. Flags are per-member now, so
  // another account's "skip" in the same browser no longer suppresses it.
  // Hook lives ABOVE every conditional return (rules of hooks); all its
  // conditions are checked inside.
  const connectedRH = !!(member?.robinhood?.status === "connected" && member?.robinhood?.account_number);
  useEffect(() => {
    if (!member || connectedRH || isLegal || isMktPage || pathname === "/welcome") return;
    if (isAdminHost() || member.needs_email_verification) return;
    if (getWizardFlag(WIZARD_SKIP_KEY, member.id) || getWizardFlag(WIZARD_DONE_KEY, member.id)) return;
    router.replace("/welcome");
  }, [member, connectedRH, isLegal, isMktPage, pathname, router]);

  // ── Marketing subpages ───────────────────────────────────────────────────
  // Rendered as-is for everyone, exactly like Robinhood shows its product
  // pages whether or not you're logged in. The children ARE the page (each
  // route wraps itself in MktShell). ABOVE the `ready` gate on purpose: these
  // are the SEO surface, and behind the gate their prerendered HTML would be
  // nothing but the auth LoadingScreen — a crawler would index a spinner. No
  // host check here either: the host helpers are false during SSR, so a
  // server/client fork on them would hydrate two different trees. MktShell
  // owns the sibling-host bounce (admin/demo/beta have no marketing surface)
  // as a client effect instead.
  if (isMktPage) return <>{children}</>;

  if (!ready) return <LoadingScreen label="starting concordia" />;

  // ── The public front door ────────────────────────────────────────────────
  // Signed out at the root of the member site, a visitor now gets the
  // marketing page instead of a bare sign-in card; sign-in moved to /join.
  //
  // Deliberately ONLY "/" — a signed-out deep link (/vote, /account, a link
  // someone was sent) still lands on AuthGate, so signing in returns them
  // where they were headed rather than dumping them on a sales page.
  //
  // The three sibling hosts keep exactly what they had: admin.* has its own ops
  // sign-in, and demo.*/beta.* ARE the pitch — a visitor arrives there to use
  // the thing, not to read about it.
  if (!member) {
    const isPublicFrontDoor =
      pathname === "/" && !isAdminHost() && !isDemoHost() && !isBetaHost();
    if (isPublicFrontDoor) return <MarketingSite />;
    return <AuthGate notice={signedOutNote} onAuthed={() => refreshMember()} />;
  }

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
    // Tighter than the app pages on BOTH ends: every wizard step should fit a
    // laptop viewport without scrolling. pb-24 (96px) is right for app pages
    // that scroll past a fixed nav, but on a self-contained step it was most of
    // the reason step 1 spilled off a 780px viewport by 88px — the step's own
    // escape-hatch link supplies the breathing room underneath.
    return (
      <main className="mx-auto w-[min(1080px,100%)] px-4 pb-8 pt-6 sm:px-6 sm:pb-10 sm:pt-8">
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
      {/* No footer: the Nav already carries the wordmark, and a strapline
          under every page was just repeating it (owner call). */}
      <main className={`mx-auto w-[min(1080px,100%)] px-4 pb-24 ${hasBanner ? "pt-4" : "pt-[88px]"} sm:px-6`}>
        {children}
      </main>
    </>
  );
}
