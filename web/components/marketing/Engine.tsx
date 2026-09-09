"use client";

// Home's centerpiece and the one genuinely new section of the 2026-09-09
// reframing: the accuracy-weighted power engine, told as mechanism only.
//
// Every claim here is verified against copy that was itself checked against
// backend source (Faq.tsx, the "How is voting power calculated?" answer and
// the file's header comment — cited by question text, not ordinal, so a
// reorder can't rot this pointer):
//   * power blends capital committed with historical accuracy
//   * accuracy is scored from closing prices in the cycle after each vote
//   * power is recomputed on a schedule, not in reaction to any one trade
//   * a large balance alone does not buy the club
//   * the formula is deliberately unpublished
// The POWER_FORM constraint binds: two score forms exist in the backend, so
// this section stays form-agnostic — mechanism named, formula never specified.
// And the section promises NOTHING about outcomes: the closing statement says
// so in as many words.

import { Reveal, Section, SectionHead, Glow } from "./primitives";

type Beat = { n: string; title: string; body: string };

const BEATS: Beat[] = [
  {
    n: "01",
    title: "Spend your ballot",
    body:
      "One ballot per cycle, spread across the S&P 500 names you want held. " +
      "The capital you commit is part of your weight — skin in the game counts.",
  },
  {
    n: "02",
    title: "Every pick gets scored",
    body:
      "Accuracy is scored from closing prices in the cycle after each vote. " +
      "Not reputation, not seniority, not who talks the most — closing prices.",
  },
  {
    n: "03",
    title: "Say shifts to whoever's right",
    body:
      "Power is recomputed on its own schedule, never in reaction to one trade. " +
      "Members who keep being right carry more of the next decision — and a " +
      "large balance alone doesn't buy the club.",
  },
];

export function Engine() {
  return (
    <Section id="engine" className="py-24 sm:py-32">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-[1]">
        <Glow className="-right-40 top-16" size={560} tone="green" intensity={0.1} />
      </div>

      <SectionHead
        eyebrow="The engine"
        title="The club keeps score."
        lead="Voting power isn't one-member-one-vote, and it isn't whoever-has-the-most-money. It's earned — and it keeps being re-earned, pick after pick."
      />

      {/* Same editorial rail as the other three-beat grids: left rules, no
          boxes. Deliberately NO diagram of members gaining or losing say — any
          drawing with countable nodes reads as a headcount (SPEC §0). */}
      <div className="mt-14 grid gap-y-9 sm:mt-20 md:grid-cols-3 md:gap-x-8 lg:gap-x-12">
        {BEATS.map((b, i) => (
          <Reveal
            key={b.n}
            delay={i * 90}
            className="border-t border-[color:var(--mkt-hair)] pt-6 md:border-t-0 md:border-l md:border-l-[color:var(--mkt-hair)] md:pl-6 md:pt-0 lg:pl-8"
          >
            <div className="mkt-mono text-[var(--mkt-subtle)]">{b.n}</div>
            <h3 className="mkt-display mkt-h3 mt-4 text-[var(--mkt-ink)]">{b.title}</h3>
            <p className="mt-3.5 text-[15px] leading-[1.68] text-[var(--mkt-muted)]">{b.body}</p>
          </Reveal>
        ))}
      </div>

      {/* The epistemic cap. First sentence pair is the section's honesty
          clause; the second is the "How is voting power calculated?" answer's
          fenced formula line, verbatim. */}
      <Reveal delay={120} className="mt-16 sm:mt-20">
        <div className="max-w-[760px] border-l border-[color:var(--mkt-green)] pl-6 sm:pl-9">
          <p className="mkt-display mkt-h3 text-[var(--mkt-ink)]">
            That doesn&rsquo;t make the club right. It makes the club listen
            better.
          </p>
          <p className="mkt-lead mt-4 max-w-[52ch]">
            We describe the mechanism; we don&rsquo;t publish the formula.
          </p>
        </div>
      </Reveal>
    </Section>
  );
}

export default Engine;
