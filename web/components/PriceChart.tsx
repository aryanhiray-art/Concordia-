"use client";

// The one price chart for both stock surfaces (the slide-over panel and the
// full research page). No chart library in this app — this is hand-rolled SVG.
//
// Why it looks the way it does (the old one collided with itself):
//   • The SVG is drawn in MEASURED pixel coordinates (ResizeObserver → viewBox
//     "0 0 W H", preserveAspectRatio default). The old chart used a fixed 400×160
//     box with preserveAspectRatio="none", which stretches everything — text
//     included — differently at panel width vs full-page width, so nothing could
//     be positioned reliably.
//   • The high/low price labels live in a RIGHT GUTTER that is carved out of the
//     box before the path is plotted. The line is never drawn there, so a label
//     physically cannot sit on top of the curve, at any width.
//   • Dates get their own bottom band (also outside the plot), first anchored to
//     the start, last to the end, so they can't spill past either edge.
//   • A flat series (min === max) centres the line and prints ONE label — no
//     division by a zero span.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { StockPricePoint } from "@/lib/types";
import { fmtTradeDate } from "@/lib/format";
import { fmtPrice } from "@/lib/stocknum";

const UP = "#2dd4bf";      // --teal
const DOWN = "#ff6b6b";    // --red

/** Catmull-Rom → cubic beziers: a smooth line through every close. */
function smoothPath(pts: [number, number][]): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1],
      p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}

export function PriceChart({
  points, height = 160, interactive = false,
}: {
  points: StockPricePoint[];
  height?: number;
  /** Full-page variant: hover crosshair + a readout in its own band above the plot. */
  interactive?: boolean;
}) {
  const [w, setW] = useState(480);          // replaced on mount by the real width
  const [hover, setHover] = useState<number | null>(null);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");

  // A CALLBACK ref, not useRef + useEffect([]): the "no history" placeholder
  // below returns a DIFFERENT element that never carried the ref, so on the
  // normal path (mount empty while loading → data arrives) a mount-once effect
  // saw wrap.current === null, bailed, and never re-ran — leaving every chart
  // pinned to the 480px default and mis-drawn at real width. A callback ref
  // fires on whichever node actually mounts, so measuring can't be skipped.
  // Width is measured, not assumed — the SVG is drawn in real pixel coordinates,
  // so a stale `w` letterboxes the whole chart (viewBox 982 inside a 301px box
  // shrinks it to a third of its height and centres it in dead space).
  //
  // A CALLBACK ref, not useRef + useEffect([]): the "no history" placeholder
  // below returns a DIFFERENT element that never carried the ref, so a
  // mount-once effect saw null on the normal path (mount empty while loading →
  // data arrives), bailed, and never re-ran. A callback ref fires on whichever
  // node actually mounts, and React hands it null on unmount, so it also owns
  // teardown — deliberately NOT a separate cleanup effect.
  const node = useRef<HTMLDivElement | null>(null);
  const ro = useRef<ResizeObserver | null>(null);
  const measure = useCallback(() => {
    const px = node.current?.getBoundingClientRect().width ?? 0;
    if (px > 0) setW(Math.round(px));
  }, []);
  const attach = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect();
    ro.current = null;
    node.current = el;
    if (!el) return;
    measure();                                   // first paint, before any resize
    if (typeof ResizeObserver !== "undefined") {
      const obs = new ResizeObserver(measure);
      obs.observe(el);
      ro.current = obs;
    }
  }, [measure]);
  // Belt and braces for environments without a live ResizeObserver. Measuring
  // on mount (above) is what actually guarantees a correct chart at any width;
  // this listener is the second path for viewport-driven container changes.
  //
  // NOT verifiable in this project's embedded preview browser: it resizes via a
  // CDP metrics override, so innerWidth changes while `resize` NEVER fires and
  // ResizeObserver delivers nothing at all — not even the initial notification
  // it is specified to always emit (measured: a fresh element, observed then
  // widened, got 0 callbacks). Both re-measure paths are therefore untested
  // there; verify responsiveness in a real browser before trusting it.
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  if (points.length < 2) {
    return (
      <div className="grid place-items-center rounded-[12px] border border-hair bg-white/[0.02] text-[12px] text-subtle"
        style={{ height: height + 24 }}>
        no price history for this range
      </div>
    );
  }

  const cs = points.map((p) => p.c);
  const mn = Math.min(...cs), mx = Math.max(...cs);
  const span = mx - mn;
  const flat = !(span > 1e-9);            // also catches NaN → treated as flat

  // ── the box: plot rectangle + the bands that keep labels off the curve ────
  const HEAD = interactive ? 18 : 0;      // hover readout band
  const PAD_T = 12 + HEAD;
  const PAD_B = 22;                       // date band
  const PAD_L = 4;
  const hiTxt = fmtPrice(mx), loTxt = fmtPrice(mn);
  const GUT = Math.max(46, Math.max(hiTxt.length, loTxt.length) * 6.3 + 14);
  const W = Math.max(220, w);
  const H = height;
  const plotW = Math.max(40, W - PAD_L - GUT);
  const plotH = Math.max(40, H - PAD_T - PAD_B);
  const plotB = PAD_T + plotH;
  const gx = PAD_L + plotW + 8;           // gutter text origin

  const X = (i: number) => PAD_L + (i / (points.length - 1)) * plotW;
  const Y = (c: number) => (flat ? PAD_T + plotH / 2 : PAD_T + (1 - (c - mn) / span) * plotH);

  const pts: [number, number][] = cs.map((c, i) => [X(i), Y(c)]);
  const up = cs[cs.length - 1] >= cs[0];
  const color = up ? UP : DOWN;
  const line = smoothPath(pts);
  const area = `${line} L${(PAD_L + plotW).toFixed(2)},${plotB} L${PAD_L},${plotB} Z`;
  const hiIdx = cs.indexOf(mx), loIdx = cs.indexOf(mn);

  // date ticks: as many as the width can hold without crowding
  const nTicks = Math.max(2, Math.min(5, Math.floor(plotW / 108)));
  const ticks = Array.from({ length: nTicks }, (_, k) =>
    Math.round((k / (nTicks - 1)) * (points.length - 1)));

  const hi = hover != null ? points[hover] : null;

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!interactive) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width) return;
    const px = (e.clientX - r.left) * (W / r.width);
    const t = (px - PAD_L) / plotW;
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(t * (points.length - 1)))));
  }

  return (
    <div ref={attach} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block touch-pan-y"
        role="img" aria-label={`price history, ${fmtTradeDate(points[0].d)} to ${fmtTradeDate(points[points.length - 1].d)}`}
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <defs>
          <linearGradient id={`pcf-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.22" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* level guides — the high and low sit exactly on the plot edges, which
            is what lets their labels live in the gutter at the same height */}
        {!flat && [PAD_T, plotB].map((y) => (
          <line key={y} x1={PAD_L} x2={PAD_L + plotW} y1={y} y2={y}
            stroke="rgba(255,255,255,0.10)" strokeWidth="1" strokeDasharray="3 4" />
        ))}
        <line x1={PAD_L} x2={PAD_L + plotW} y1={PAD_T + plotH / 2} y2={PAD_T + plotH / 2}
          stroke="rgba(255,255,255,0.06)" strokeWidth="1" />

        <path d={area} fill={`url(#pcf-${uid})`} />
        <path d={line} fill="none" stroke={color} strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" />

        {/* high / low markers + their gutter labels */}
        {!flat && (
          <>
            <circle cx={X(hiIdx)} cy={Y(mx)} r="2.5" fill={color} />
            <circle cx={X(loIdx)} cy={Y(mn)} r="2.5" fill={color} />
            <text x={gx} y={PAD_T} dominantBaseline="middle" textAnchor="start"
              className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{hiTxt}</text>
            <text x={gx} y={plotB} dominantBaseline="middle" textAnchor="start"
              className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{loTxt}</text>
          </>
        )}
        {flat && (
          <text x={gx} y={PAD_T + plotH / 2} dominantBaseline="middle" textAnchor="start"
            className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{hiTxt}</text>
        )}

        {/* date band — first left-anchored, last right-anchored, never past an edge */}
        {ticks.map((i, k) => (
          <text key={i} x={X(i)} y={H - 6}
            textAnchor={k === 0 ? "start" : k === ticks.length - 1 ? "end" : "middle"}
            className="tnum" fill="rgba(255,255,255,0.38)" fontSize="10.5">
            {fmtTradeDate(points[i].d)}
          </text>
        ))}

        {/* hover crosshair + readout (its own band, so it never covers the line) */}
        {hi && (
          <>
            <line x1={X(hover!)} x2={X(hover!)} y1={PAD_T} y2={plotB}
              stroke="rgba(255,255,255,0.22)" strokeWidth="1" />
            <circle cx={X(hover!)} cy={Y(hi.c)} r="3.5" fill={color} stroke="#0b0f14" strokeWidth="1.5" />
            <text x={PAD_L} y={10} dominantBaseline="middle" textAnchor="start" className="tnum"
              fill="rgba(255,255,255,0.72)" fontSize="11">
              {fmtTradeDate(hi.d)} · {fmtPrice(hi.c)}
            </text>
          </>
        )}
      </svg>
    </div>
  );
}
