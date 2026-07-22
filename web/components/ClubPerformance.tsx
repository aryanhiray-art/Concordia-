"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PerfView } from "@/lib/types";

// Inline SVG line chart (no chart library): the club's decision basket vs SPY
// buy & hold, both indexed to 100 at the first decided session. All text lives
// in HTML (legend above, dates below) so it stays legible at any width — the
// SVG scales only the lines.
const W = 720, H = 200;
const PAD = { t: 10, r: 10, b: 10, l: 10 };

function pct(p: number | null): string {
  return p == null ? "—" : `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
}
function day(d: string): string {
  return new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
// Catmull-Rom → cubic beziers, for gently smoothed lines
function smooth(pts: number[][]): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)}`
      + ` ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)}`
      + ` ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

export function ClubPerformance() {
  const [perf, setPerf] = useState<PerfView | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  useEffect(() => {
    api.clubPerformance()
      .then((p) => { setPerf(p); setState("ok"); })
      .catch(() => setState("error"));
  }, []);

  if (state === "error") {
    return (
      <div className="text-[12.5px] text-subtle">
        Performance chart unavailable right now — it&apos;ll be back shortly.
      </div>
    );
  }
  if (state === "loading") return <div className="pulse h-[150px] rounded-[12px] bg-white/[0.04]" />;

  const pts = perf?.points ?? [];
  if (!pts.length) {
    return (
      <div className="text-[12.5px] text-subtle">
        No club trades yet — the graph appears once the basket runs live.
      </div>
    );
  }

  const innerW = W - PAD.l - PAD.r, innerH = H - PAD.t - PAD.b;
  const vals = pts.flatMap((p) => [p.club, p.spy]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  // Floor the y-range at 1.5 index points so a near-flat first week isn't
  // amplified to full chart height (a ±0.1% wiggle reading as a moonshot).
  const mid = (hi + lo) / 2, span = Math.max(hi - lo, 1.5);
  const breathe = span * 0.08;
  lo = mid - span / 2 - breathe; hi = mid + span / 2 + breathe;
  const x = (i: number) => PAD.l + (pts.length <= 1 ? 0.5 : i / (pts.length - 1)) * innerW;
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * innerH;

  // subtle gridlines on rounded index levels (~4 of them)
  const step = [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100].find((s) => (hi - lo) / s <= 5) ?? 100;
  const g0 = Math.ceil(lo / step) * step;
  const grid: number[] = [];
  for (let k = 0; g0 + k * step <= hi; k++) grid.push(Math.round((g0 + k * step) * 100) / 100);

  const club = pts.map((p, i) => [x(i), y(p.club)]);
  const spy = pts.map((p, i) => [x(i), y(p.spy)]);
  const sign = (p: number | null) => (p == null ? "var(--muted)" : p >= 0 ? "var(--green)" : "var(--red)");

  return (
    <div>
      {/* legend + headline numbers (HTML, not SVG — legible on any screen) */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[12.5px] font-semibold tnum">
        <span className="flex items-center gap-2">
          <span className="inline-block h-[3px] w-4 rounded-full bg-teal" />
          <span style={{ color: sign(perf!.club_pct) }}>Club {pct(perf!.club_pct)}</span>
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block h-[2px] w-4 rounded-full bg-white/35" />
          <span style={{ color: sign(perf!.spy_pct) }}>S&amp;P 500 {pct(perf!.spy_pct)}</span>
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 block w-full" role="img"
        aria-label={`Club ${pct(perf!.club_pct)} vs S&P 500 ${pct(perf!.spy_pct)} since ${perf!.start_date ?? "inception"}`}>
        {grid.map((g) => (
          <line key={g} x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} stroke="rgba(255,255,255,0.06)" />
        ))}
        <path d={smooth(spy)} fill="none" stroke="var(--subtle)" strokeWidth="1.6" strokeLinecap="round" opacity="0.75" />
        <path d={smooth(club)} fill="none" stroke="var(--teal)" strokeWidth="2.2" strokeLinecap="round" />
        {pts.length === 1 && (
          <>
            <circle cx={x(0)} cy={y(pts[0].spy)} r="3" fill="var(--subtle)" />
            <circle cx={x(0)} cy={y(pts[0].club)} r="3.5" fill="var(--teal)" />
          </>
        )}
      </svg>

      <div className="mt-1 flex justify-between text-[11px] text-subtle tnum">
        <span>{day(pts[0].d)}</span>
        {pts.length > 1 && <span>{day(pts[pts.length - 1].d)}</span>}
      </div>
      <div className="mt-1 text-[11px] text-subtle">
        Basket decisions, equal-weight, vs SPY buy &amp; hold — not your account balance.
      </div>
    </div>
  );
}
