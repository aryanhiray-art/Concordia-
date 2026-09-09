"use client";

// The verified decisions index vs SPY, restyled for the marketing surface.
//
// The maths is the member app's ClubPerformance chart, unchanged: index both
// series to 100 at the first decided session, floor the y-range so a flat week
// is not amplified into a moonshot, and smooth with Catmull-Rom. Still no
// charting library — a line and an area fill do not justify 40kB, and an inline
// SVG is the only version that is correct on the first paint.
//
// What changed for this surface: bigger, calmer, an area fill under the club
// line, a hairline at the inception level, and mono axis labels rendered as
// HTML on top of the SVG rather than as <text> inside it — SVG text scales with
// the viewBox, so an 11px label becomes 4px on a 360px phone.

import { type PublicStatsPoint } from "@/lib/publicApi";
import { CTA, Eyebrow, Glow, Reveal, Section } from "./primitives";
import { fmtIso, pct, usePublicStats } from "@/lib/usePublicStats";

const W = 900, H = 360;
// Left pad is the gutter the mono level labels sit in (desktop only).
const PAD = { t: 26, r: 22, b: 30, l: 54 };

/** Catmull-Rom → cubic beziers. Returns "" for fewer than two points, which is
 *  the single-point case: the caller draws dots instead of a path. */
function smooth(pts: Array<[number, number]>): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i];
    const p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)}`
      + ` ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)}`
      + ` ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

type Geometry = {
  grid: number[];
  gridY: (v: number) => number;
  clubD: string;
  areaD: string;
  spyD: string;
  clubEnd: [number, number];
  spyEnd: [number, number] | null;
};

/** All the chart geometry in one place so the JSX below stays readable, and so
 *  the empty case is a single `null` the renderer can branch on. */
function geometry(pts: PublicStatsPoint[]): Geometry | null {
  if (pts.length === 0) return null;

  // One null benchmark point poisons the whole SPY path with NaN, so the line
  // is all-or-nothing rather than broken into segments.
  const hasSpy = pts.every((p) => typeof p.spy === "number");
  const vals = pts.flatMap((p) => (typeof p.spy === "number" ? [p.club, p.spy] : [p.club]));

  let lo = Math.min(...vals), hi = Math.max(...vals);
  // Floor the visible range at 1.5 index points: without it a ±0.1% first week
  // fills the chart top to bottom and reads as violent movement.
  const mid = (hi + lo) / 2, span = Math.max(hi - lo, 1.5);
  const breathe = span * 0.1;
  lo = mid - span / 2 - breathe;
  hi = mid + span / 2 + breathe;

  const innerW = W - PAD.l - PAD.r, innerH = H - PAD.t - PAD.b;
  const x = (i: number) => PAD.l + (pts.length <= 1 ? 0.5 : i / (pts.length - 1)) * innerW;
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * innerH;

  // Gridlines on round index levels, ~4 of them at any zoom.
  const step = [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100].find((sp) => (hi - lo) / sp <= 5) ?? 100;
  const first = Math.ceil(lo / step) * step;
  const grid: number[] = [];
  for (let k = 0; first + k * step <= hi; k++) grid.push(Math.round((first + k * step) * 100) / 100);

  const clubPts: Array<[number, number]> = pts.map((p, i) => [x(i), y(p.club)]);
  const spyPts: Array<[number, number]> = hasSpy
    ? pts.map((p, i) => [x(i), y(p.spy as number)])
    : [];

  const clubD = smooth(clubPts);
  const baseY = H - PAD.b;
  const areaD = clubD
    ? `${clubD} L${clubPts[clubPts.length - 1][0].toFixed(1)},${baseY}`
      + ` L${clubPts[0][0].toFixed(1)},${baseY} Z`
    : "";

  return {
    grid,
    gridY: y,
    clubD,
    areaD,
    spyD: smooth(spyPts),
    clubEnd: clubPts[clubPts.length - 1],
    spyEnd: spyPts.length ? spyPts[spyPts.length - 1] : null,
  };
}

/** A terminal dot, placed over the chart in viewBox percentages. The halo is a
 *  second element rather than a box-shadow so it inherits the same var() colour
 *  at a plain opacity, with no colour maths. */
function Marker({ x, y, color, size, halo = false }: {
  x: number; y: number; color: string; size: number; halo?: boolean;
}) {
  const dot = (px: number, opacity: number) => (
    <span
      aria-hidden
      className="absolute block rounded-full"
      style={{
        left: `${(x / W) * 100}%`,
        top: `${(y / H) * 100}%`,
        width: px, height: px,
        marginLeft: -px / 2, marginTop: -px / 2,
        background: color,
        opacity,
      }}
    />
  );
  return (
    <>
      {halo && dot(size * 2.4, 0.18)}
      {dot(size, 1)}
    </>
  );
}

/* ── Legend ────────────────────────────────────────────────────────────────── */

function Series({ swatch, name, value, tone }: {
  swatch: React.ReactNode | null; name: string; value: string; tone: string;
}) {
  // `swatch` is nullable on purpose: a line key that points at a line the chart
  // is not drawing (no series at all, or a benchmark dropped because one day's
  // price was missing) tells the reader to look for something that is not
  // there. The figure beside it is still true, so only the key goes away.
  return (
    <div className="flex items-baseline gap-2.5">
      {swatch}
      <span className="mkt-mono text-[var(--mkt-muted)]">{name}</span>
      <span className="mkt-display tnum text-[clamp(1.1rem,2.2vw,1.5rem)]" style={{ color: tone }}>
        {value}
      </span>
    </div>
  );
}

export function TrackRecord() {
  const s = usePublicStats();
  const g = geometry(s.points);

  const startIso = s.points.length ? s.points[0].d : s.live_since;
  const endIso = s.points.length ? s.points[s.points.length - 1].d : null;

  return (
    <Section id="record" className="py-24 sm:py-32">
      <Glow className="-top-16 left-1/2 -translate-x-1/2" tone="green" intensity={0.12} size={640} />

      {/* z-index above the Glow, which is absolute at z-0 inside this section */}
      <div className="relative z-[1]">
        <div className="max-w-[760px]">
          {/* The page hero above (/record) carries "The record" and the old
              title; this head now introduces the chart itself, honest-negative
              first — the disclosure below is the long form of the same claim. */}
          <Reveal><Eyebrow>The index</Eyebrow></Reveal>
          <Reveal delay={60}>
            <h2 className="mkt-display mkt-h2 mt-5 text-[var(--mkt-ink)]">
              The index, and what it is not.
            </h2>
          </Reveal>
          <Reveal delay={120}>
            {/* The basket count is a live fact and is stated as one. The
                verification claim is fenced to the window it was actually run
                over — SPEC §2's 24 baskets through 2026-08-24. Stating it in the
                present tense ("the index is recomputed, with prices
                spot-checked") would read as a standing process and quietly
                extend the claim to every basket decided since. */}
            <p className="mkt-lead mt-5">
              Every basket the club has decided — <span className="tnum text-[var(--mkt-ink)]">
                {s.n_sessions}
              </span>{" "}
              of them — equal-weighted and marked at daily closes, against the S&amp;P 500
              over the same window. The index is built from the locked baskets
              themselves. The figures through 24 August 2026 were independently
              recomputed, every price spot-checked against Robinhood.
            </p>
          </Reveal>
        </div>

        <Reveal delay={160}>
          <figure className="mkt-card mt-12 p-5 sm:mt-16 sm:p-8">
            {/* Legend + headline figures. HTML, never SVG <text>: legible at
                360px and selectable. */}
            <figcaption className="flex flex-wrap items-baseline gap-x-8 gap-y-4">
              <Series
                name="Decisions index (paper)"
                value={pct(s.club_pct)}
                tone="var(--mkt-green)"
                swatch={g?.clubD ? (
                  <span aria-hidden className="h-[3px] w-5 flex-none rounded-full bg-[var(--mkt-green)]" />
                ) : null}
              />
              <Series
                name="S&P 500 buy & hold"
                value={pct(s.spy_pct)}
                tone="var(--mkt-muted)"
                swatch={g?.spyD ? (
                  <span
                    aria-hidden
                    className="h-[2px] w-5 flex-none rounded-full"
                    style={{
                      backgroundImage:
                        "repeating-linear-gradient(90deg, var(--mkt-subtle) 0 5px, transparent 5px 9px)",
                    }}
                  />
                ) : null}
              />
            </figcaption>

            {g ? (
              <>
                <div className="relative mt-7">
                  <svg
                    viewBox={`0 0 ${W} ${H}`}
                    // Height is set in CSS and the aspect deliberately NOT
                    // preserved: locked to the viewBox ratio the chart collapses
                    // to ~45px of plot on a 360px phone, which is a sparkline,
                    // not evidence. Stretching the box instead keeps the same
                    // maths at every width; `vector-effect: non-scaling-stroke`
                    // on every stroked node stops the non-uniform scale from
                    // distorting line weights, and the round markers are HTML
                    // overlays below rather than <circle>s, which would ellipse.
                    preserveAspectRatio="none"
                    className="block h-[210px] w-full sm:h-[290px] lg:h-[360px]"
                    role="img"
                    aria-label={
                      `Decisions index ${pct(s.club_pct)} versus S&P 500 ${pct(s.spy_pct)}, `
                      + `both indexed to 100 on ${fmtIso(startIso)}`
                      + (endIso ? ` through ${fmtIso(endIso)}.` : ".")
                    }
                  >
                    <defs>
                      <linearGradient id="mkt-record-area" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--mkt-green)" stopOpacity="0.22" />
                        <stop offset="100%" stopColor="var(--mkt-green)" stopOpacity="0" />
                      </linearGradient>
                    </defs>

                    {g.grid.map((level) => (
                      <line
                        key={level}
                        x1={PAD.l} x2={W - PAD.r}
                        y1={g.gridY(level)} y2={g.gridY(level)}
                        stroke="var(--mkt-hair)"
                        // The inception level is the only line that means
                        // anything — above it the basket is up. Give it weight.
                        strokeWidth={level === 100 ? 1.4 : 1}
                        strokeDasharray={level === 100 ? undefined : "2 6"}
                        vectorEffect="non-scaling-stroke"
                      />
                    ))}

                    {/* mkt-chart-area / mkt-chart-draw: the line draws itself
                        in when the surrounding Reveal gains .is-in, then the
                        area wash fades up (marketing.css). pathLength={1}
                        makes the draw's dash math geometry-independent. The
                        figures are untouched — this is arrival, not data. */}
                    {g.areaD && <path className="mkt-chart-area" d={g.areaD} fill="url(#mkt-record-area)" />}
                    {g.spyD && (
                      <path d={g.spyD} fill="none" stroke="var(--mkt-subtle)" strokeWidth="1.8"
                        strokeDasharray="6 6" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                    )}
                    {g.clubD && (
                      <path className="mkt-chart-draw" d={g.clubD} pathLength={1} fill="none"
                        stroke="var(--mkt-green)" strokeWidth="2.6"
                        strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
                    )}
                  </svg>

                  {/* Overlays in the SVG's own coordinate space, expressed as
                      percentages — they track the geometry at any width, stay
                      perfectly round under the stretched viewBox, and render
                      mono text at a real px size instead of a scaled one. */}
                  {g.spyEnd && (
                    <Marker x={g.spyEnd[0]} y={g.spyEnd[1]} color="var(--mkt-subtle)" size={7} />
                  )}
                  {/* With a single decided session the markers ARE the chart. */}
                  <Marker x={g.clubEnd[0]} y={g.clubEnd[1]} color="var(--mkt-green)" size={9} halo />

                  {g.grid.map((level) => (
                    <span
                      key={level}
                      aria-hidden
                      className="mkt-mono absolute left-0 hidden text-[10px] text-[var(--mkt-subtle)] sm:block"
                      style={{ top: `${(g.gridY(level) / H) * 100}%`, transform: "translateY(-50%)" }}
                    >
                      {Number.isInteger(level) ? level.toFixed(0) : level.toFixed(1)}
                    </span>
                  ))}
                </div>

                <div className="mkt-mono mt-3 flex items-center justify-between gap-3 text-[10px] text-[var(--mkt-subtle)]">
                  <span className="tnum">{fmtIso(startIso)}</span>
                  <span className="hidden sm:inline">Indexed to 100 at the first decided session</span>
                  {endIso && <span className="tnum">{fmtIso(endIso)}</span>}
                </div>
              </>
            ) : (
              // No points: the figures above are still true and still shown.
              // A missing series is not a reason to hide the record.
              <p className="mkt-mono mt-8 border-t border-[var(--mkt-hair)] pt-8 text-[10px] leading-[1.7] text-[var(--mkt-subtle)]">
                The daily series is unavailable right now. The figures above are the
                verified totals for {fmtIso(s.live_since)} – {fmtIso(s.as_of)} — a
                closed window, not a running total.
              </p>
            )}

            {/* SPEC §2 disclosure, verbatim, inside the same block as the chart —
                not the footer, not a tooltip, and at reading size. */}
            <hr className="mkt-rule my-7" />
            <p className="max-w-[68ch] text-[12.5px] leading-[1.65] text-[var(--mkt-muted)]">
              The decisions index is a paper, equal-weight model of the baskets the club
              voted for, marked at daily closes. It is not any member&rsquo;s account return:
              it excludes fees, slippage, order minimums and the timing of real fills. Past
              performance does not guarantee future results.
            </p>
          </figure>
        </Reveal>

        {/* The quiet call to action under the chart: the record is the point
            where a reader either believes it or wants to poke at it (since
            the five-page split, this section IS /record's body). Pointing
            them at the sandbox rather than at signup is also the honest ask —
            the demo runs this same code on fake money, so it answers the
            question the chart just raised without anyone connecting a
            brokerage account first. */}
        <Reveal delay={80}>
          <div className="mt-10 flex flex-col items-start gap-4 sm:mt-12 sm:flex-row sm:items-center sm:justify-between">
            <p className="max-w-[46ch] text-[14px] leading-relaxed text-[var(--mkt-muted)]">
              You don&rsquo;t need an invite to look. The demo runs this same code
              against fake money, with the club&rsquo;s real record on the chart.
            </p>
            <CTA href="https://demo.voteconcordia.com" variant="ghost" external className="flex-none">
              Open the demo
            </CTA>
          </div>
        </Reveal>
      </div>
    </Section>
  );
}
