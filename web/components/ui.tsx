"use client";

import { useEffect, useState } from "react";
import { fmtDateTime, fmtTime } from "@/lib/format";

export function Logo({ size = 40 }: { size?: number }) {
  // The concordia mark: three concentric "C" arcs, gaps facing right —
  // outer mint, middle deeper mint, inner dark-sage core.
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden>
      <g className="lg-outer">
        <circle cx="50" cy="50" r="40" fill="none" stroke="#A8E4CE" strokeWidth="11"
          strokeLinecap="round" pathLength={360} strokeDasharray="285 75"
          transform="rotate(37.5 50 50)" />
      </g>
      <g className="lg-mid">
        <circle cx="50" cy="50" r="26.5" fill="none" stroke="#7FD1B6" strokeWidth="10.5"
          strokeLinecap="round" pathLength={360} strokeDasharray="270 90"
          transform="rotate(45 50 50)" />
      </g>
      <g className="lg-core">
        <circle cx="50" cy="50" r="13" fill="none" stroke="#4C8570" strokeWidth="10"
          strokeLinecap="round" pathLength={360} strokeDasharray="250 110"
          transform="rotate(55 50 50)" />
      </g>
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="font-display text-[19px] font-bold tracking-tight">
      concordia
    </span>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`glass shine p-5 sm:p-6 ${className}`}>{children}</div>;
}

type Variant = "go" | "glass" | "ok" | "danger" | "ghost";

export function Button({
  children, onClick, variant = "glass", disabled, busy, className = "", type = "button",
}: {
  children: React.ReactNode; onClick?: () => void; variant?: Variant;
  disabled?: boolean; busy?: boolean; className?: string; type?: "button" | "submit";
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled || busy}
      className={`btn btn-${variant} ${className}`}>
      {busy && <span className="spin" />}
      {children}
    </button>
  );
}

type Tone = "ok" | "teal" | "warn" | "danger" | "neutral";
export function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  const cls = tone === "neutral" ? "" : `pill-${tone}`;
  return <span className={`pill ${cls}`}>{children}</span>;
}

export function Dot({ tone = "ok", pulse = false }: { tone?: "ok" | "warn" | "danger" | "gray"; pulse?: boolean }) {
  const bg = { ok: "var(--green)", warn: "var(--amber)", danger: "var(--red)", gray: "var(--subtle)" }[tone];
  return <span className={`dot ${pulse ? "pulse" : ""}`} style={{ background: bg }} aria-hidden />;
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "teal" | "green" | "red" }) {
  const color = tone === "teal" ? "text-teal" : tone === "green" ? "text-green" : tone === "red" ? "text-red" : "text-ink";
  return (
    <div className="glass glass-2 rounded-[14px] px-4 py-3.5">
      <div className="label">{label}</div>
      <div className={`mt-1.5 font-display text-[22px] font-bold leading-none tnum ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[12px] text-subtle">{sub}</div>}
    </div>
  );
}

export function AllocRow({ ticker, pct, votes, max }: { ticker: string; pct?: number; votes?: number; max: number }) {
  const width = max > 0 ? Math.max(4, ((votes ?? 0) / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-[58px] font-display text-[14px] font-bold">{ticker}</span>
      <span className="bar flex-1"><i style={{ width: `${width}%` }} /></span>
      <span className="w-[44px] text-right font-display text-[13px] font-semibold text-muted tnum">
        {pct != null ? `${pct}%` : votes}
      </span>
    </div>
  );
}

export function Dots() {
  return <span className="dots"><span /><span /><span /></span>;
}

export function LoadingScreen({ label = "loading" }: { label?: string }) {
  return (
    <div className="grid min-h-screen place-items-center">
      <div className="flex flex-col items-center gap-4">
        <span className="loadmark"><Logo size={56} /></span>
        <span className="text-[12px] text-subtle">{label}…</span>
      </div>
    </div>
  );
}

// The money on screen comes from a cached snapshot, so say so — a footnote,
// not a banner. Quiet + subtle while fresh; amber with a manual pull once the
// backend flags it stale. Renders nothing when there's nothing honest to claim
// (no as_of from the backend and not stale).
export function Freshness({
  asOf, stale = false, busy = false, onRefresh, className = "",
}: {
  asOf: number | null;          // epoch SECONDS
  stale?: boolean;
  busy?: boolean;
  onRefresh?: () => void;
  className?: string;
}) {
  if (!asOf && !stale) return null;
  let when = "";
  if (asOf) {
    // A snapshot from another day must not masquerade as "9:42 this morning".
    const sameDay = new Date(asOf * 1000).toDateString() === new Date().toDateString();
    when = sameDay ? fmtTime(asOf) : fmtDateTime(asOf);
  }
  return (
    <span className={`inline-flex items-center gap-2 text-[11.5px] ${stale ? "text-amber" : "text-subtle"} ${className}`}>
      <span className="tnum">{when ? `as of ${when}` : "may be out of date"}</span>
      {stale && onRefresh && (
        <button type="button" onClick={onRefresh} disabled={busy}
          className="font-semibold underline underline-offset-2 transition hover:text-ink disabled:opacity-50">
          {busy ? "refreshing…" : "Refresh"}
        </button>
      )}
    </span>
  );
}

export function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-6 w-11 flex-none rounded-full transition disabled:opacity-50
        ${on ? "bg-teal-deep shadow-[0_0_18px_-4px_rgba(45,212,191,0.8)]" : "bg-white/12"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

export function Chip({ children, onRemove }: { children: React.ReactNode; onRemove?: () => void }) {
  return (
    <span className="enter inline-flex items-center gap-1.5 rounded-full border border-teal/40 bg-teal/[0.1] px-2.5 py-1 font-display text-[12.5px] font-semibold text-ink">
      {children}
      {onRemove && (
        <button onClick={onRemove} aria-label="remove" className="text-teal/70 hover:text-teal">×</button>
      )}
    </span>
  );
}

export function Countdown({ to }: { to: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  const left = Math.max(0, Math.floor(to - now));
  if (left <= 0) return <span className="text-red">closed</span>;
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  return <span className="tnum">{h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`}</span>;
}
