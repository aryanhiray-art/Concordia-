"use client";

import type { PowerView } from "@/lib/types";
import { Card } from "./ui";

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const w = 220, h = 44;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / span) * (h - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-3 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? "var(--green)" : "var(--red)"} strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function VotingPowerCard({ power }: { power: PowerView | null }) {
  if (!power) return null;
  const d = power.delta;
  const hasDelta = d != null && d !== 0;
  const up = (d ?? 0) >= 0;

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <h2 className="label">Your voting power</h2>
          <div className="mt-1.5 font-display text-[30px] font-bold leading-none tnum">
            {money(power.power)}
          </div>
          <div className="mt-1.5 text-[12px] text-subtle">
            {hasDelta ? (
              <span className={up ? "text-green" : "text-red"}>
                {up ? "▲" : "▼"} {money(Math.abs(d!))} this week
              </span>
            ) : power.as_of_week ? "no change this week" : "set on your first Sunday after connecting"}
            {power.as_of_week && <span className="text-subtle"> · as of {power.as_of_week}</span>}
          </div>
        </div>
        <span className="pill pill-teal">stake-weighted</span>
      </div>
      <Sparkline values={power.history.map((h) => h.power)} />
      <p className="mt-2 text-[11.5px] text-subtle">
        Your voting power equals your account value, re-allocated every Sunday. Bigger stake → more say in the basket.
      </p>
    </Card>
  );
}
