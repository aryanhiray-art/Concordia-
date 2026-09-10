"use client";

// The voting-power dial that opens /club — the marketing hero's convergence
// figure, repurposed as an instrument (owner spec, 2026-09-09): the member's
// number sits in the center, and the green arc on the outer ring IS that
// number — the full circle is the whole club's say, the arc is your slice.
// Clicking the dial unfolds the statistics beneath it.
//
// Adaptation rules (from the marketing original, Hero.tsx):
//   * geometry is module-scope deterministic arithmetic — never Math.random —
//     so server and client render byte-identical markup;
//   * no countable "member" nodes: 48 hairlines read as texture, not roster;
//   * the dashed dial ring carries NO transform attribute (the standalone
//     .mkt-dial CSS animation owns its transform); the progress arc's
//     rotation lives in the ATTRIBUTE, so only its dash/opacity may animate;
//   * def ids are namespaced club-dial-* to avoid colliding with the
//     marketing hero if both ever mount in one document;
//   * colors are APP tokens (--green/--teal/--hair/…): the --mkt-* variables
//     don't resolve outside .mkt, and wrapping app content in .mkt would
//     paint over the aurora.
//
// Data: api.socialMe() (routes/social.py) — every field nullable; a member
// before their first reweight sees an em-dash and a reason, never a zero.

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SocialPowerStats } from "@/lib/types";
import { money } from "@/components/wizard/money";

const C = 210; // centre of the 420×420 viewBox

const SPOKES = Array.from({ length: 48 }, (_, i) => {
  const a = (i / 48) * Math.PI * 2 - Math.PI / 2;
  const major = i % 4 === 0;
  const at = (r: number) =>
    [Number((C + Math.cos(a) * r).toFixed(2)), Number((C + Math.sin(a) * r).toFixed(2))] as const;
  const [x1, y1] = at(major ? 186 : 170);
  const [x2, y2] = at(104);
  return { x1, y1, x2, y2, o: major ? 0.55 : 0.18 };
});

/** rAF count-up to a target, once, honoring reduced motion. */
function useCountUp(target: number | null, ms = 1100): number | null {
  const [shown, setShown] = useState<number | null>(null);
  const done = useRef(false);
  useEffect(() => {
    if (target == null) { setShown(null); done.current = false; return; }
    if (done.current) { setShown(target); return; }
    done.current = true;
    // reduced motion AND hidden documents get the final value instantly —
    // rAF is suspended in hidden tabs, and a dial that waits for frames
    // would sit at an em-dash until the tab is refocused.
    if (document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(target);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(target * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return shown;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="glass rise p-4">
      <div className="label">{label}</div>
      <div className="mt-1.5 font-display text-[22px] font-semibold tracking-tight text-ink tnum">
        {value}
      </div>
      {sub && <div className="mt-1 text-[11.5px] leading-snug text-subtle">{sub}</div>}
    </div>
  );
}

/** Tiny power-trend sparkline from the weekly history /api/me already ships. */
function Spark({ points }: { points: number[] }) {
  const d = useMemo(() => {
    if (points.length < 2) return "";
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    return points
      .map((p, i) => {
        const x = (i / (points.length - 1)) * 180 + 2;
        const y = 34 - ((p - min) / span) * 28 - 2;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  }, [points]);
  if (!d) return null;
  return (
    <svg viewBox="0 0 184 36" className="h-9 w-full" aria-hidden>
      <path
        d={d}
        fill="none"
        stroke="var(--green)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        // draws itself in when the panel opens — the panel remounts the
        // sparkline on expand, so a keyframe on mount is all it takes
        style={{ strokeDasharray: 1, strokeDashoffset: 0, animation: "clubspark 900ms var(--ease) both" }}
      />
    </svg>
  );
}

export function ClubPowerDial() {
  const { power } = useAuth();
  const [stats, setStats] = useState<SocialPowerStats | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  // bumped on every OPEN — keys the sparkline so it redraws its line each
  // time the panel unfolds (it draws on mount)
  const [openSeq, setOpenSeq] = useState(0);
  // bumped on EVERY click — keys the arc so its sweep-from-zero keyframe
  // replays; the first mount plays it on page load
  const [arcSeq, setArcSeq] = useState(0);

  useEffect(() => {
    let dead = false;
    api.socialMe()
      .then((s) => { if (!dead) setStats(s); })
      // a failed fetch must NOT render like the legitimate pre-reweight
      // state — "sets at the first reweight" would be a false statement
      // about the member's standing
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; };
  }, []);

  // share as a 0..100 percentage, or null before the first snapshot
  const pct = stats?.share != null ? stats.share * 100 : null;
  const shown = useCountUp(pct);

  // the arc: pathLength=360 makes the dash array read as degrees. A true 0%
  // share draws NO arc — the 1.5° legibility floor is only for small-but-real
  // slices. The sweep-in is the .club-arc keyframe (globals.css), which runs
  // from 0° to this value on every mount of the keyed circle below — no rAF
  // arming needed, CSS animations ride the wall clock even in hidden tabs.
  const sweep = pct != null && pct > 0 ? Math.max(1.5, (pct / 100) * 360) : 0;

  const histPoints = (power?.history ?? []).map((h) => h.power).filter((n) => n != null) as number[];

  return (
    <section aria-label="Your voting power">
      <button
        type="button"
        onClick={() => {
          setArcSeq((n) => n + 1);
          setOpen((v) => { if (!v) setOpenSeq((n) => n + 1); return !v; });
        }}
        aria-expanded={open}
        aria-controls="club-dial-stats"
        className="group relative mx-auto block w-[min(320px,78vw)] cursor-pointer rounded-full outline-offset-8 transition-transform duration-200 active:scale-[0.985] motion-reduce:transition-none sm:w-[360px]"
      >
        <svg viewBox="0 0 420 420" className="h-auto w-full" aria-hidden>
          <defs>
            <radialGradient id="club-dial-core" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="var(--green)" stopOpacity="0.16" />
              <stop offset="42%" stopColor="var(--teal)" stopOpacity="0.07" />
              <stop offset="100%" stopColor="var(--teal)" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="club-dial-fade" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="white" stopOpacity="1" />
              <stop offset="60%" stopColor="white" stopOpacity="0.9" />
              <stop offset="82%" stopColor="white" stopOpacity="0.3" />
              <stop offset="100%" stopColor="white" stopOpacity="0" />
            </radialGradient>
            <mask id="club-dial-mask">
              <rect width="420" height="420" fill="url(#club-dial-fade)" />
            </mask>
          </defs>

          <circle cx={C} cy={C} r={180} fill="url(#club-dial-core)" />

          <g mask="url(#club-dial-mask)">
            {SPOKES.map((s, i) => (
              <line
                key={i}
                x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
                stroke="var(--teal)" strokeWidth={1} opacity={s.o * 0.5}
              />
            ))}
          </g>

          {/* the club: a full, faint ring — everyone's say, including yours */}
          <circle cx={C} cy={C} r={196} fill="none" stroke="var(--hair)" strokeWidth={2} />
          <circle cx={C} cy={C} r={205} fill="none" stroke="var(--hair)" strokeWidth={1} opacity={0.6} />

          {/* your slice of it — dash degrees == your share of the circle.
              Rotation is the ATTRIBUTE (start at 12 o'clock); the dash is the
              only thing CSS animates, so the two never fight. */}
          {sweep > 0 && (
            <circle
              key={arcSeq}
              className="club-arc"
              cx={C} cy={C} r={196} fill="none"
              stroke="var(--green)" strokeWidth={3} strokeLinecap="round"
              pathLength={360}
              strokeDasharray={`${sweep} ${360 - sweep}`}
              transform={`rotate(-90 ${C} ${C})`}
            />
          )}

          {/* the dial ring — slow ambient rotation via the standalone
              .mkt-dial animation; no transform attribute here, CSS owns it */}
          <circle
            className="club-dial"
            cx={C} cy={C} r={100} fill="none"
            stroke="var(--hair-warm)" strokeWidth={1}
            strokeDasharray="1.5 9" strokeLinecap="round"
          />
        </svg>

        {/* the number, centered over the SVG */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          {/* .hero-num's font shorthand owns the size (52px) — don't fight it
              with utilities; the .sm variant exists if it ever crowds. */}
          <div className="hero-num">
            {shown == null ? "—" : `${shown.toFixed(1)}%`}
          </div>
          <div className="label mt-2">of the club&rsquo;s say</div>
          <div className="mt-1 text-[12px] text-subtle tnum">
            {failed
              ? "couldn't load your power right now"
              : stats == null
                ? "…"
                : stats.power_usd != null && stats.as_of_week
                  ? `${money(stats.power_usd)} voting power`
                  : "sets at the first reweight after you commit"}
          </div>
          <div className="mt-3 text-[10.5px] uppercase tracking-[0.14em] text-subtle opacity-70 transition-opacity duration-300 group-hover:opacity-100">
            {open ? "hide statistics" : "tap for statistics"}
          </div>
        </div>
      </button>

      {/* The reveal, choreographed BOTH ways (owner ask, 2026-09-10):
          content stays mounted so the close can animate — an unmounting
          panel just vanishes. Opening: the box unfolds (grid-rows 0fr→1fr)
          and the tiles rise in left-to-right, 70ms apart, after a 120ms
          beat so the box leads. Closing: the tiles cascade OUT in reverse
          (last in, first out, 40ms apart) while the box waits 160ms and
          then folds over them. No measuring, no portals; reduced motion
          collapses every step to an instant cut. */}
      <div
        id="club-dial-stats"
        aria-hidden={!open}
        className="grid transition-[grid-template-rows,opacity] duration-500 [transition-timing-function:var(--ease)] motion-reduce:transition-none"
        style={{
          gridTemplateRows: open ? "1fr" : "0fr",
          opacity: open ? 1 : 0,
          transitionDelay: open ? "0ms" : "160ms",
        }}
      >
        <div className="overflow-hidden">
          {stats && (
            <div className="mx-auto mt-6 grid max-w-[720px] gap-3 pb-1 sm:grid-cols-3">
              {[
                <Stat
                  key="power"
                  label="voting power"
                  value={stats.power_usd != null && stats.as_of_week ? money(stats.power_usd) : "—"}
                  sub={stats.as_of_week ? `as of ${stats.as_of_week}` : "no snapshot yet"}
                />,
                // rank alone, no "of N" — a member headcount stays off member
                // surfaces, the same posture the ballot takes with voter counts
                <Stat key="rank" label="club rank"
                  value={stats.rank != null ? `#${stats.rank}` : "—"}
                  sub="across the active club" />,
                <Stat key="stake" label="committed stake"
                  value={stats.capital_usd != null ? money(stats.capital_usd) : "—"}
                  sub="what the club may use" />,
                // accuracy ×100 into points: real EWMA values live at ±0.0x
                // and would all collapse to "+0.00" at raw 2dp. Normalize -0
                // so a flat record never renders the broken "-0.00".
                <Stat key="acc" label="accuracy score"
                  value={(() => {
                    if (stats.accuracy == null) return "—";
                    const pts = Math.round(stats.accuracy * 100 * 100) / 100 + 0;
                    const v = Object.is(pts, -0) ? 0 : pts;
                    return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
                  })()}
                  sub="signed score vs benchmark, ×100 — not a return" />,
                <Stat key="cycles" label="cycles scored"
                  value={stats.cycles_scored != null ? String(stats.cycles_scored) : "—"}
                  sub="closed cycles behind your record" />,
                <div key="trend" className="glass rise p-4">
                  <div className="label">power trend</div>
                  {histPoints.length >= 2 ? (
                    // keyed by openSeq: remounts per open, so the line draws
                    // itself again each time the panel unfolds
                    <div className="mt-2"><Spark key={openSeq} points={histPoints} /></div>
                  ) : (
                    <div className="mt-1.5 font-display text-[22px] font-semibold text-ink">—</div>
                  )}
                  <div className="mt-1 text-[11.5px] leading-snug text-subtle">
                    weekly snapshots — say is re-earned, not banked
                  </div>
                </div>,
              ].map((tile, i, all) => (
                <div
                  key={i}
                  className="motion-reduce:!transition-none"
                  style={{
                    opacity: open ? 1 : 0,
                    transform: open ? "none" : "translateY(14px) scale(0.98)",
                    transition: "opacity 450ms var(--ease), transform 450ms var(--ease)",
                    transitionDelay: open
                      ? `${120 + i * 70}ms`
                      : `${(all.length - 1 - i) * 40}ms`,
                  }}
                >
                  {tile}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
