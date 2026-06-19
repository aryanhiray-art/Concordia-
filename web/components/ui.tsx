"use client";

import { useEffect, useState } from "react";

export function Logo({ size = 40 }: { size?: number }) {
  return (
    <span className="mark" style={{ width: size, height: size }}>
      <svg viewBox="0 0 24 24" aria-hidden>
        <line x1="12" y1="12" x2="12" y2="4.5" />
        <line x1="12" y1="12" x2="18.5" y2="8.25" />
        <line x1="12" y1="12" x2="18.5" y2="15.75" />
        <line x1="12" y1="12" x2="12" y2="19.5" />
        <line x1="12" y1="12" x2="5.5" y2="15.75" />
        <line x1="12" y1="12" x2="5.5" y2="8.25" />
        <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
      </svg>
    </span>
  );
}

export function Wordmark() {
  return (
    <span className="font-display text-[19px] font-bold tracking-tight">
      con<span className="text-teal">·</span>cordia
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
        <span className="pulse"><Logo size={48} /></span>
        <Dots />
        <span className="text-[12px] text-subtle">{label}…</span>
      </div>
    </div>
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
