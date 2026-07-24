"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Logo } from "./ui";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/vote", label: "Vote" },
  { href: "/history", label: "History" },
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
        <Logo size={30} />
        <b className="cnav-word font-display text-[15px] font-bold tracking-tight text-[#8FD9C0]">
          concordia
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
          className="cnav-out rounded-full border border-hair px-3 py-1.5 text-[12px] text-muted transition hover:text-ink">
          Log out
        </button>
      </div>
    </nav>
  );
}
