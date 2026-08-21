"use client";

// The console's pinned chrome: brand + environment on the left, the five page
// tabs in the middle, freshness + the two global actions on the right. The
// admin host has no <Nav>, so this header IS the shell — every page renders
// under it and the tabs are real next/link hrefs (/, /members, /cycle, /bots,
// /audit) so browser back/forward works. AppShell renders <AdminConsole /> for
// EVERY admin-host path; the console maps unknown paths to Overview here.
//
// position:FIXED (+ an in-flow spacer), not sticky: globals.css sets
// html,body{overflow-x:hidden}, which makes body a scroll context and silently
// defeats every descendant position:sticky — the header just scrolled away.
// The member site's .cnav is fixed for the same reason; this follows suit.
// Heights are deterministic (rows are h-11 / h-10), so the spacer can match:
// 44+40+1 with the wrapped tab row below lg, 44+1 inline at lg+.

import { Button, Logo } from "@/components/ui";
import { AdminLink, OpsTag } from "./util";

export type AdminPageKey = "overview" | "basket" | "members" | "cycle" | "bots" | "audit";

/** Route → page. Unknown paths render Overview (per the host contract). */
export function pageKeyFor(pathname: string | null | undefined): AdminPageKey {
  switch (pathname) {
    case "/basket": return "basket";
    case "/members": return "members";
    case "/cycle": return "cycle";
    case "/bots": return "bots";
    case "/audit": return "audit";
    default: return "overview";
  }
}

const TABS: { key: AdminPageKey; href: string; label: string }[] = [
  { key: "overview", href: "/", label: "Overview" },
  { key: "basket", href: "/basket", label: "Basket" },
  { key: "members", href: "/members", label: "Members" },
  { key: "cycle", href: "/cycle", label: "Cycle" },
  { key: "bots", href: "/bots", label: "Bots" },
  { key: "audit", href: "/audit", label: "Audit" },
];

export function OpsHeader({ page, updatedAt, busy, onRefresh, onLogout }: {
  page: AdminPageKey;
  updatedAt: Date | null;
  busy: boolean;
  onRefresh: () => void;
  onLogout: () => void;
}) {
  return (
    <>
    <header className="fixed inset-x-0 top-0 z-50 border-b border-hair bg-[rgba(7,10,14,0.82)] backdrop-blur-xl">
      <div className="flex w-full flex-wrap items-center px-4 sm:px-6">
        {/* brand cluster — wordmark + OPS tag (the env chip stays on the
            sign-in landing only; in-console it was just another word) */}
        <AdminLink href="/" className="flex h-11 flex-none items-center gap-2">
          <Logo size={22} />
          {/* wordmark yields on phones (member-nav precedent) so brand +
              Refresh/Log out share the first row; logo + ops tag stay */}
          <b className="hidden font-display text-[14px] font-bold tracking-tight text-[#8FD9C0] sm:block">concordia</b>
          <OpsTag />
        </AdminLink>

        {/* page tabs — second row that scrolls sideways on narrow screens,
            inline on desktop; active tab gets the teal underline */}
        <nav
          aria-label="Console pages"
          className="order-last -mx-4 flex w-[calc(100%+2rem)] items-stretch gap-0.5 overflow-x-auto px-4 lg:order-none lg:mx-0 lg:ml-5 lg:w-auto lg:px-0"
        >
          {TABS.map((t) => {
            const on = t.key === page;
            return (
              <AdminLink
                key={t.key}
                href={t.href}
                aria-current={on ? "page" : undefined}
                className={`relative flex h-10 flex-none items-center px-3 text-[12.5px] transition lg:h-11 ${
                  on ? "font-semibold text-ink" : "font-medium text-muted hover:text-ink"
                }`}
              >
                {t.label}
                {on && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2.5 bottom-0 h-[2px] rounded-full bg-gradient-to-r from-teal to-teal-deep shadow-[0_0_10px_rgba(45,212,191,0.6)]"
                  />
                )}
              </AdminLink>
            );
          })}
        </nav>

        {/* freshness + global actions */}
        <div className="ml-auto flex h-11 flex-none items-center gap-2.5 pl-3">
          <span className="hidden font-mono text-[11px] text-subtle tnum md:block">
            {updatedAt
              ? `updated ${updatedAt.toLocaleTimeString(undefined, { hour12: false })}`
              : "loading…"}
          </span>
          <Button variant="glass" busy={busy} onClick={onRefresh}>
            Refresh
          </Button>
          <button
            onClick={onLogout}
            className="rounded-full border border-hair px-3 py-1.5 text-[12px] text-muted transition hover:text-ink"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
    {/* in-flow stand-in for the fixed header, so content starts below it */}
    <div aria-hidden className="h-[85px] lg:h-[45px]" />
    </>
  );
}
