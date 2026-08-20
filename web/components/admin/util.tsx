"use client";

// Shared helpers for the admin console panels (components/admin/*). Every
// panel reads the club's trading clock, so the ET-pinned formatter lives here
// rather than lib/format (whose helpers render in the viewer's zone).

import Link from "next/link";
import { Pill } from "@/components/ui";

/** In-console navigation link. The console's pages (/members, /cycle, /bots,
 *  /audit) deliberately have NO app/ routes — AppShell renders <AdminConsole/>
 *  for every admin-host pathname — so a plain next/link transition 404s its
 *  RSC fetch and Next falls back to a FULL document reload: auth restarts,
 *  all four admin fetches refire, the loading screen flashes. Next ≥14.1 syncs
 *  usePathname() with raw history.pushState, so pushing the URL re-renders the
 *  console instantly with the data it already holds (back/forward keep working
 *  via popstate). Modified/middle clicks keep native behavior — the server
 *  renders the console on any path, so a hard load still lands right. */
export function AdminLink({ href, className, children, ...rest }: {
  href: string;
  className?: string;
  children: React.ReactNode;
  "aria-current"?: "page";
}) {
  return (
    <Link href={href} prefetch={false} className={className} {...rest}
      onClick={(e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        if (window.location.pathname !== href) window.history.pushState(null, "", href);
        window.scrollTo(0, 0);
      }}>
      {children}
    </Link>
  );
}

/** Epoch seconds (or ms, or ISO string — defensive) → "h:mm AM ET". */
export function fmtET(ts: number | string | null | undefined, withDate = false): string {
  if (ts == null) return "—";
  const d = typeof ts === "number" ? new Date(ts * (ts > 1e12 ? 1 : 1000)) : new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  // Deliberately NOT lib/format's helpers: the ops console must read in ET
  // (the club's trading clock), not the viewer's zone. Date shape still
  // MM/DD/YYYY — "en-US" + 2-digit month/day gives exactly that.
  const opts: Intl.DateTimeFormatOptions = {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
    ...(withDate ? { month: "2-digit", day: "2-digit", year: "numeric" } : {}),
  };
  return `${d.toLocaleString("en-US", opts)} ET`;
}

export function money(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function stake(type: string | null | undefined, value: number | null | undefined): string {
  if (value == null || !type) return "—";
  return type === "percent" ? `${value}%` : money(value);
}

/** Bot accuracy = last scored weekly return as a fraction → signed percent. */
export function fmtAccuracy(a: number | null | undefined): string {
  if (a == null) return "—";
  const pct = a * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/** Red for hard failures, amber for everything else that's off. */
export function exceptionTone(e: string): "danger" | "warn" {
  return /error|fail|revoked|expired|halt|disconnect|missing/i.test(e) ? "danger" : "warn";
}

/** club_perf_cached arrives shape-unknown — render whatever is legible. */
export function perfLine(p: unknown): string | null {
  if (p == null) return null;
  if (typeof p === "string") return p;
  if (typeof p === "number") return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`;
  if (typeof p === "object") {
    const o = p as Record<string, unknown>;
    const club = o.club_pct ?? o.club;
    const spy = o.spy_pct ?? o.spy;
    const pct = (v: unknown) => (typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "—");
    if (club != null || spy != null) return `club ${pct(club)} vs SPY ${pct(spy)}`;
    try { return JSON.stringify(p); } catch { return null; }
  }
  return String(p);
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="label mb-3">{children}</p>;
}

/** Card header row: the uppercase section label + an optional right-aligned
 *  slot (a link, a chip, a toggle) sharing the label's baseline. One grammar
 *  for every panel head, so the pages read as one product. */
export function SectionHead({ label, right }: {
  label: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <SectionLabel>{label}</SectionLabel>
      {right && <div className="mb-3 ml-auto flex items-center gap-2.5">{right}</div>}
    </div>
  );
}

/** The teal "deeper page →" link that panel heads carry on the Overview. */
export function MoreLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <AdminLink href={href} className="text-[12px] font-medium text-teal hover:underline">
      {children} →
    </AdminLink>
  );
}

/** Table-header micro-label. Deliberately NOT the .label class: it sets
 *  display:block, which knocks a <th> out of table-cell layout and stacks the
 *  header row into a diagonal stair. Compose alignment per column. */
export const TH_FONT = "pb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle";

// ── ops identity (shared by the console chrome AND the sign-in landing) ──────

/** One rule for both surfaces: the loud teal PROD cue is reserved for the
 *  deployed console — admin.localhost dev, LAN IPs, anything else reads as a
 *  muted LOCAL. SSR-safe guard only (neither surface server-renders). */
export function envIsProd(): boolean {
  return typeof window !== "undefined" &&
    window.location.hostname === "admin.voteconcordia.com";
}

/** The PROD/LOCAL environment chip — identical on the landing and in the
 *  console header, so the door and the room visibly belong to each other. */
export function EnvChip({ className = "" }: { className?: string }) {
  const prod = envIsProd();
  return (
    <span className={`inline-flex w-fit items-center gap-1.5 rounded-full border px-2 py-[4px] font-mono text-[10px] font-semibold leading-none tracking-[0.16em] ${
      prod ? "border-teal/35 bg-teal/[0.08] text-teal" : "border-hair bg-white/[0.04] text-muted"
    } ${className}`}>
      <span className={`h-[5px] w-[5px] rounded-full ${prod ? "bg-teal" : "bg-subtle"}`} aria-hidden />
      {prod ? "PROD" : "LOCAL"}
    </span>
  );
}

/** The mono OPS tag that rides the wordmark on both surfaces. */
export function OpsTag({ className = "" }: { className?: string }) {
  return (
    <span className={`font-mono font-bold uppercase leading-none tracking-[0.22em] text-teal ${className || "text-[10px]"}`}>
      ops
    </span>
  );
}

/** Deferrals ride inside `error` on skipped rows — the executor's cash-account
 *  "not enough SETTLED cash yet" path — and the owner monitors both-day runs
 *  by exactly this count, so it gets its own detector + rendering. */
export function isDeferred(error?: string | null): boolean {
  return /deferred/i.test(error || "");
}

type Tone = "ok" | "teal" | "warn" | "danger" | "neutral";

export function execTone(status: string, error?: string | null): Tone {
  if (isDeferred(error)) return "warn";
  switch (status) {
    case "placed": return "ok";
    case "reviewed": return "teal";
    case "skipped": return "warn";
    case "error": return "danger";
    default: return "neutral"; // planned + anything the executor grows later
  }
}

// sell = amber (positions leave), buy = green (basket fills), both = teal
// (the mwf single-session run), idle = neutral.
const PHASE_TONE: Record<string, Tone> = { sell: "warn", buy: "ok", both: "teal", idle: "neutral" };

export function PhaseChip({ phase }: { phase: string }) {
  return <Pill tone={PHASE_TONE[phase] ?? "neutral"}>{phase}</Pill>;
}

/** The member's agentic-account type: limited margin redeploys same-day, a
 *  cash account waits T+1 for proceeds (its buys defer), null = not recorded. */
export function AcctTypeBadge({ type }: { type?: string | null }) {
  if (!type) return <span className="text-subtle">—</span>;
  if (type === "limited_margin" || type === "margin") return <Pill tone="teal">margin</Pill>;
  if (type === "cash") return <Pill tone="warn">cash — delayed</Pill>;
  return <Pill tone="neutral">{type}</Pill>;
}
