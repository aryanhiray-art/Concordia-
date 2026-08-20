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
//   • The high/low price labels sit INSIDE the plot, right-aligned just above/
//     below their dashed guide lines (Robinhood-style) — the old reserved right
//     gutter kept ~70px of every chart permanently empty.
//   • Dates get their own bottom band (also outside the plot), first anchored to
//     the start, last to the end, so they can't spill past either edge.
//   • A flat series (min === max) centres the line and prints ONE label — no
//     division by a zero span.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { StockPricePoint } from "@/lib/types";
import { fmtTradeDate } from "@/lib/format";
import { fmtPrice } from "@/lib/stocknum";

/** Epoch seconds for a point, from `t` (intraday) or `d` (daily, parsed as a
 *  PLAIN local date — `new Date("2026-07-28")` is UTC midnight and lands on the
 *  previous day for US viewers). NaN when neither is usable, which makes the
 *  caller fall back to index spacing. */
function tsOf(p: StockPricePoint): number {
  if (typeof p.t === "number" && Number.isFinite(p.t)) return p.t;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(p.d || "");
  return m ? new Date(+m[1], +m[2] - 1, +m[3]).getTime() / 1000 : NaN;
}

/** How the axis / readout labels a point.
 *
 *  "auto" = the stock behaviour (time-of-day for intraday series, MM/DD/YYYY for
 *  daily ones). The explicit modes exist because the ACCOUNT chart plots one
 *  continuous recorded series across every window — 10-minute observations near
 *  the present, thinned to daily further back — so the right label is a property
 *  of the RANGE the member picked, not of the sample spacing. */
export type ChartLabelMode = "auto" | "time" | "weekday" | "monthday" | "monthyear";

const p2 = (n: number) => String(n).padStart(2, "0");

/** The point's instant, or null when it carries no usable date at all. */
function dateOf(p: StockPricePoint): Date | null {
  const s = tsOf(p);
  return Number.isFinite(s) ? new Date(s * 1000) : null;
}

/** Axis/readout label for a point: time-of-day for intraday, date for daily. */
function labelOf(p: StockPricePoint, intraday: boolean, mode: ChartLabelMode = "auto"): string {
  if (mode !== "auto") {
    const d = dateOf(p);
    if (!d) return "—";
    if (mode === "time") return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (mode === "weekday") return `${d.toLocaleDateString(undefined, { weekday: "short" })} ${p2(d.getMonth() + 1)}/${p2(d.getDate())}`;
    if (mode === "monthday") return `${p2(d.getMonth() + 1)}/${p2(d.getDate())}`;
    return `${p2(d.getMonth() + 1)}/${d.getFullYear()}`;   // "monthyear"
  }
  if (intraday && typeof p.t === "number") {
    return new Date(p.t * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  return fmtTradeDate(p.d);
}

const UP = "#2dd4bf";      // --teal
const DOWN = "#ff6b6b";    // --red

/** Straight segments through every close — the only shape that cannot lie.
 *
 *  This was Catmull-Rom → cubic beziers (control points at ±(p2−p0)/6). That
 *  curve OVERSHOOTS: measured against a series bounded 0..100 its control
 *  points reached 116.7, and replayed against real payloads the drawn path
 *  exceeded the data's true high/low on 13 of 15 symbol/range pairs — XOM 3M
 *  data [131.08, 148.67] rendered as [130.08, 149.24], a $0.57 high that never
 *  traded. Worse, this chart pins its high/low guide lines and their gutter
 *  labels to the data's min/max, so the curve visibly crossed the very numbers
 *  it was labelled with.
 *
 *  A price chart must never draw a price that did not happen, so there is no
 *  smoothing at all — the vertices ARE the closes. (Robinhood and Yahoo draw
 *  it this way for the same reason.) If softening is ever wanted, it must be
 *  monotone/shape-preserving, never Catmull-Rom.
 */
function linePath(pts: [number, number][]): string {
  if (pts.length < 2) return "";
  return pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(" ");
}

export function PriceChart({
  points, height = 160, interactive = false, labelMode = "auto", onHover, ariaLabel,
}: {
  points: StockPricePoint[];
  height?: number;
  /** Full-page variant: hover crosshair + a readout in its own band above the plot. */
  interactive?: boolean;
  /** Axis/readout label format. Default "auto" = infer from the series shape. */
  labelMode?: ChartLabelMode;
  /** Delegate the readout: when given, the hovered index is reported to the
   *  parent and this chart draws NO readout text of its own (the account
   *  overview shows it in the hero, at hero size). null = pointer left. */
  onHover?: (i: number | null) => void;
  /** Overrides the screen-reader description (default describes a price series). */
  ariaLabel?: string;
}) {
  const [w, setW] = useState(480);          // replaced on mount by the real width
  const [hover, setHover] = useState<number | null>(null);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");

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

  // Intraday when the series carries epoch timestamps (5-minute bars); daily
  // ranges carry only `d`. Drives whether labels read as times or dates.
  const intraday = typeof points[0]?.t === "number";
  const cs = points.map((p) => p.c);
  const mn = Math.min(...cs), mx = Math.max(...cs);
  const span = mx - mn;
  const flat = !(span > 1e-9);            // also catches NaN → treated as flat

  // ── the box: plot rectangle + the bands that keep labels off the curve ────
  const delegated = typeof onHover === "function";   // parent draws the readout
  const HEAD = interactive && !delegated ? 18 : 0;   // hover readout band
  // 17, not 12: the high label's baseline sits at PAD_T - 5, so the pad must
  // hold a full 10.5px line of text or the top of "$110.95" is shaved off at
  // the viewBox edge (it was, on the account hero — the delegated case where
  // HEAD contributes nothing).
  const PAD_T = 17 + HEAD;
  const PAD_B = 22;                       // date band
  const PAD_L = 0;
  const hiTxt = fmtPrice(mx), loTxt = fmtPrice(mn);
  // Labels are drawn INSIDE the plot (right-aligned above/below their guide
  // lines, Robinhood-style) instead of in a reserved right gutter — the gutter
  // left ~70px of dead space and the owner wants the line edge to edge.
  const GUT = 0;
  const W = Math.max(220, w);
  const H = height;
  const plotW = Math.max(40, W - PAD_L - GUT);
  const plotH = Math.max(40, H - PAD_T - PAD_B);
  const plotB = PAD_T + plotH;

  // x is positioned by TIME, not array index. Index spacing draws a Friday→Monday
  // step the same width as Monday→Tuesday, and an overnight gap the same width as
  // five minutes — the market's closed hours silently vanish and the line implies
  // trading that never happened. Falling back to index only when timestamps are
  // absent or degenerate (all-equal), where even spacing is the honest default.
  const ts = points.map(tsOf);
  const t0 = ts[0], t1 = ts[ts.length - 1];
  const tSpan = t1 - t0;
  const byTime = Number.isFinite(tSpan) && tSpan > 0;
  const X = (i: number) => PAD_L + (byTime ? (ts[i] - t0) / tSpan : i / (points.length - 1)) * plotW;
  const Y = (c: number) => (flat ? PAD_T + plotH / 2 : PAD_T + (1 - (c - mn) / span) * plotH);

  const pts: [number, number][] = cs.map((c, i) => [X(i), Y(c)]);
  const up = cs[cs.length - 1] >= cs[0];
  const color = up ? UP : DOWN;
  const line = linePath(pts);
  const area = `${line} L${(PAD_L + plotW).toFixed(2)},${plotB} L${PAD_L},${plotB} Z`;
  const hiIdx = cs.indexOf(mx), loIdx = cs.indexOf(mn);

  // Date ticks: as many as the width can hold without crowding, chosen by TIME
  // and then snapped to the nearest real point.
  //
  // Evenly-spaced INDICES look identical on a uniformly-sampled series and are
  // wrong the moment the sampling isn't uniform — the account series is
  // 10-minute near the present and daily further back, so index-spaced ticks
  // pile up in the recent sliver ("07/27 07/3008/01" printed on top of each
  // other) while months of chart carry no label at all. x is time-positioned,
  // so the ticks have to be too.
  const nTicks = Math.max(2, Math.min(5, Math.floor(plotW / 108)));
  const nearestTo = (target: number) => {
    let best = 0, bd = Infinity;
    for (let i = 0; i < ts.length; i++) {
      const d = Math.abs(ts[i] - target);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  };
  const ticks = Array.from({ length: nTicks }, (_, k) => {
    const f = k / (nTicks - 1);
    return byTime ? nearestTo(t0 + f * tSpan) : Math.round(f * (points.length - 1));
  }).filter((idx, k, arr) => k === 0 || idx !== arr[k - 1]);   // a short series can snap two ticks together

  // Clamped: a range switch can leave a hover index pointing past the new,
  // shorter series (state survives, `points` does not).
  const hi = hover != null && hover >= 0 && hover < points.length ? points[hover] : null;

  function moveTo(i: number | null) {
    setHover(i);
    onHover?.(i);
  }

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!interactive) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width) return;
    const px = (e.clientX - r.left) * (W / r.width);
    // x is TIME-positioned, so the nearest point is the nearest in x — not
    // `round(t * (n-1))`, which is index arithmetic and lands on the wrong
    // observation the moment the samples aren't evenly spaced (they aren't:
    // 10-minute near the present, daily further back).
    let best = 0, bestD = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs(pts[i][0] - px);
      if (d < bestD) { bestD = d; best = i; }
    }
    moveTo(best);
  }

  return (
    <div ref={attach} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block touch-pan-y"
        role="img" aria-label={ariaLabel ?? `price history, ${labelOf(points[0], intraday, labelMode)} to ${labelOf(points[points.length - 1], intraday, labelMode)}`}
        onPointerMove={onMove} onPointerLeave={() => moveTo(null)}
        // A TAP sets the crosshair but fires no pointerleave when the finger
        // lifts, so the grey line sat pinned on whatever point was touched
        // (the owner found it parked on a Sunday). Release/cancel clear it;
        // mouse users get it straight back on the next move.
        onPointerUp={() => moveTo(null)} onPointerCancel={() => moveTo(null)}>
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
            <text x={W - 6} y={PAD_T - 5} textAnchor="end"
              className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{hiTxt}</text>
            <text x={W - 6} y={plotB - 6} textAnchor="end"
              className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{loTxt}</text>
          </>
        )}
        {flat && (
          <text x={W - 6} y={PAD_T + plotH / 2 - 6} textAnchor="end"
            className="tnum" fill="rgba(255,255,255,0.52)" fontSize="10.5">{hiTxt}</text>
        )}

        {/* date band — first left-anchored, last right-anchored, never past an edge */}
        {ticks.map((i, k) => (
          <text key={i} x={X(i)} y={H - 6}
            textAnchor={k === 0 ? "start" : k === ticks.length - 1 ? "end" : "middle"}
            className="tnum" fill="rgba(255,255,255,0.38)" fontSize="10.5">
            {labelOf(points[i], intraday, labelMode)}
          </text>
        ))}

        {/* hover crosshair + readout (its own band, so it never covers the line) */}
        {hi && (
          <>
            <line x1={X(hover!)} x2={X(hover!)} y1={PAD_T} y2={plotB}
              stroke="rgba(255,255,255,0.22)" strokeWidth="1" />
            <circle cx={X(hover!)} cy={Y(hi.c)} r="3.5" fill={color} stroke="#0b0f14" strokeWidth="1.5" />
            {!delegated && (
              <text x={PAD_L} y={10} dominantBaseline="middle" textAnchor="start" className="tnum"
                fill="rgba(255,255,255,0.72)" fontSize="11">
                {labelOf(hi, intraday, labelMode)} · {fmtPrice(hi.c)}
              </text>
            )}
          </>
        )}
      </svg>
    </div>
  );
}
