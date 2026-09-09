"use client";

// The one place on the page where you can SEE the thing.
//
// Everything else here argues in prose: a comparison table, a chart, an FAQ.
// A reader who has never seen a ballot still cannot picture what a "cycle"
// actually is by the time they reach the closing CTA — which is the gap the
// landing pattern for this product type names outright (hero + a live preview
// of the product, then metrics, then how it works).
//
// It is an ILLUSTRATION, and says so on the page. The alternative — rendering
// the club's real current basket — would publish the positions of a private
// club to anyone with the URL, which the public API deliberately refuses to
// serve. So the names below are fixed, obviously generic large-caps, chosen to
// show the MECHANIC (weights, a tally, a cut line) and nothing else. No
// performance figure appears anywhere in this section.

import { useEffect, useRef, useState } from "react";
import { Eyebrow, Glow, Reveal, Section } from "./primitives";

type Row = { symbol: string; name: string; mine: number; club: number };

// `mine` = one member's allocation of their own voting power.
// `club` = where the club's weighted vote landed. They differ on purpose: the
// point of the section is that your ballot is an input, not the outcome.
const ROWS: Row[] = [
  { symbol: "MSFT", name: "Microsoft",        mine: 28, club: 92 },
  { symbol: "JPM",  name: "JPMorgan Chase",   mine: 22, club: 78 },
  { symbol: "COST", name: "Costco",           mine: 20, club: 61 },
  { symbol: "UNH",  name: "UnitedHealth",     mine: 18, club: 44 },
  { symbol: "NKE",  name: "Nike",             mine: 12, club: 26 },
];

// Where the consensus cut falls in this illustration. Rows below it are voted
// on but do not make the basket — the single most misunderstood part of the
// mechanic, and the reason the cut is drawn rather than described.
const CUT = 3;

/** Bar that fills once, when the panel first reaches the viewport — the tally
 *  arriving, not decoration. Static under reduced motion. */
function Bar({ pct, dim = false, delay = 0, run }: { pct: number; dim?: boolean; delay?: number; run: boolean }) {
  return (
    <span className="relative block h-[7px] flex-1 overflow-hidden rounded-full bg-[rgba(236,242,240,0.07)]">
      <span
        className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-[900ms] ease-out motion-reduce:transition-none"
        style={{
          width: run ? `${pct}%` : "0%",
          transitionDelay: `${delay}ms`,
          background: dim
            ? "rgba(236,242,240,0.22)"
            : "linear-gradient(90deg, var(--mkt-teal), var(--mkt-green))",
        }}
      />
    </span>
  );
}

function Panel({ title, meta, children }: { title: string; meta: string; children: React.ReactNode }) {
  return (
    <div className="mkt-card p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="font-display text-[15px] font-semibold tracking-tight text-[var(--mkt-ink)]">{title}</h3>
        <span className="mkt-mono text-[var(--mkt-subtle)]">{meta}</span>
      </div>
      <div className="mt-5">{children}</div>
    </div>
  );
}

export function ProductPreview() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [run, setRun] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setRun(true); return; }
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      setRun(true);
      io.disconnect();
    }, { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <Section id="preview" className="py-24 sm:py-32 lg:py-40">
      <Glow className="-left-[14%] top-[10%]" tone="teal" intensity={0.1} size={560} />

      <div className="relative z-[1]">
        <div className="max-w-[760px]">
          <Reveal><Eyebrow>The interface</Eyebrow></Reveal>
          <Reveal delay={60}>
            <h2 className="mkt-display mkt-h2 mt-5 text-[var(--mkt-ink)]">
              Watch a consensus form.
            </h2>
          </Reveal>
          <Reveal delay={120}>
            {/* "Split ... among the names you'd back", not "Spend ... across
                the names you want held" — the Engine section directly above
                opens with that construction, and adjacent sections must not
                share their first breath. */}
            <p className="mkt-lead mt-5">
              Split your voting power among the names you&apos;d back. Everyone else does
              the same. The tally decides the basket — and the basket trades in every
              member&apos;s own account.
            </p>
          </Reveal>
        </div>

        <Reveal delay={160}>
          <div ref={ref} className="mt-12 grid gap-4 sm:mt-16 lg:grid-cols-2">
            <Panel title="Your ballot" meta="closes 09:15 et">
              <ul className="space-y-3.5">
                {ROWS.map((r, i) => (
                  <li key={r.symbol} className="flex items-center gap-3">
                    <span className="w-[104px] flex-none">
                      <span className="block font-display text-[13.5px] font-bold leading-tight text-[var(--mkt-ink)]">{r.symbol}</span>
                      <span className="mt-0.5 block truncate text-[11px] leading-tight text-[var(--mkt-subtle)]">{r.name}</span>
                    </span>
                    <Bar pct={r.mine} run={run} delay={i * 70} />
                    <span className="w-[38px] flex-none text-right font-display text-[13px] font-semibold tabular-nums text-[var(--mkt-muted)]">
                      {r.mine}%
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 border-t border-[color:var(--mkt-hair)] pt-4 text-[13px] leading-relaxed text-[var(--mkt-subtle)]">
                100% of your voting power allocated. You can change it until the ballot closes.
              </p>
            </Panel>

            <Panel title="The club's consensus" meta="weighted tally">
              <ul className="space-y-3.5">
                {ROWS.map((r, i) => {
                  const below = i >= CUT;
                  return (
                    <li key={r.symbol}>
                      {i === CUT && (
                        // The cut line, drawn rather than explained.
                        <div className="mb-3.5 flex items-center gap-3" aria-hidden>
                          <span className="h-px flex-1 bg-[var(--mkt-hair)]" />
                          <span className="mkt-mono text-[10px] text-[var(--mkt-subtle)]">below the line</span>
                          <span className="h-px flex-1 bg-[var(--mkt-hair)]" />
                        </div>
                      )}
                      <div className="flex items-center gap-3">
                        <span className="w-[104px] flex-none">
                          <span className={`block font-display text-[13.5px] font-bold leading-tight ${below ? "text-[var(--mkt-subtle)]" : "text-[var(--mkt-ink)]"}`}>
                            {r.symbol}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] leading-tight text-[var(--mkt-subtle)]">{r.name}</span>
                        </span>
                        <Bar pct={r.club} dim={below} run={run} delay={220 + i * 70} />
                        <span className={`w-[38px] flex-none text-right font-display text-[13px] font-semibold tabular-nums ${below ? "text-[var(--mkt-subtle)]" : "text-[var(--mkt-muted)]"}`}>
                          {r.club}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="mt-5 border-t border-[color:var(--mkt-hair)] pt-4 text-[13px] leading-relaxed text-[var(--mkt-subtle)]">
                The names above the line become the basket. A name you voted for can miss it —
                and one you voted against can make it. That is what a consensus is.
              </p>
            </Panel>
          </div>
        </Reveal>

        <Reveal delay={200}>
          <p className="mkt-mono mt-6 text-[var(--mkt-subtle)]">
            ◆ illustration of the interface — not the club&apos;s basket, and not a recommendation
          </p>
        </Reveal>
      </div>
    </Section>
  );
}

export default ProductPreview;
