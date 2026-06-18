"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/vote", label: "Vote" },
  { href: "/program", label: "API bot" },
  { href: "/account", label: "Account" },
];

function money(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function Nav() {
  const path = usePathname();
  const { member, power, logout } = useAuth();

  return (
    <nav className="cnav">
      <Link href="/" className="flex items-center gap-2.5">
        <span className="mark !h-[30px] !w-[30px] !rounded-[9px]">
          <svg viewBox="0 0 24 24" aria-hidden><path d="M4 15l5-6 4 4 7-8" /><path d="M4 20h16" /></svg>
        </span>
        <b className="font-display text-[15px] font-semibold tracking-tight">
          con<span className="text-teal">·</span>cordia
        </b>
      </Link>

      <div className="cnav-tabs ml-1">
        {TABS.map((t) => {
          const on = t.href === "/" ? path === "/" : path.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} className={`ctab ${on ? "on" : ""}`}>{t.label}</Link>
          );
        })}
      </div>

      <div className="ml-auto flex items-center gap-3">
        {member && (
          <span className="hidden font-display text-[12.5px] font-semibold tnum sm:block">
            <span className="font-medium text-subtle">power </span>{money(power?.power)}
          </span>
        )}
        <button onClick={logout}
          className="rounded-full border border-hair px-3 py-1.5 text-[12px] text-muted transition hover:text-ink">
          Log out
        </button>
      </div>
    </nav>
  );
}
