"use client";

// Home's subpage teasers — the Robinhood "what we offer" move, in this site's
// grammar. Each card reuses its subpage's H1 VERBATIM as the hook, so teaser
// and destination can never drift; if a subpage hero is retitled, this file is
// part of the same diff. Cards are NUMBER-FREE by rule: the paper-index
// disclosure doesn't travel to a teaser, so no figure may either (SPEC §4).

import Link from "next/link";
import { Reveal, Section, SectionHead } from "./primitives";

type Card = { href: string; label: string; hook: string; body: string };

const CARDS: Card[] = [
  {
    href: "/how-it-works",
    label: "How it works",
    hook: "Your vote is weighed, not just counted.",
    body: "Connect once, vote each cycle, one basket follows — the whole loop in three steps.",
  },
  {
    href: "/difference",
    label: "The difference",
    hook: "A vote with a memory.",
    body: "Clubs, copy-trading, and what changes when the group keeps score.",
  },
  {
    href: "/record",
    label: "The record",
    hook: "What the votes actually picked.",
    body: "The decisions index, published and marked at daily closes. Shown, not promised.",
  },
];

export function Explore() {
  return (
    <Section className="py-24 sm:py-32">
      <SectionHead eyebrow="Go deeper" title="The rest of the argument." />

      <div className="mt-12 grid gap-4 sm:mt-16 md:grid-cols-3">
        {CARDS.map((c, i) => (
          <Reveal key={c.href} delay={i * 80}>
            <Link
              href={c.href}
              className="group flex h-full flex-col rounded-[20px] border border-[color:var(--mkt-hair)] bg-[var(--mkt-panel)] p-6 transition-colors duration-300 hover:border-[color:var(--mkt-hair-warm)] hover:bg-[var(--mkt-panel-2)] sm:p-7"
            >
              <span className="mkt-mono text-[var(--mkt-subtle)]">{c.label}</span>
              <span className="mkt-display mkt-h3 mt-4 text-[var(--mkt-ink)]">{c.hook}</span>
              <span className="mt-3 text-[14.5px] leading-[1.65] text-[var(--mkt-muted)]">{c.body}</span>
              <span
                aria-hidden
                className="mkt-mono mt-auto pt-6 text-[var(--mkt-green)] opacity-70 transition-opacity duration-300 group-hover:opacity-100"
              >
                Read →
              </span>
            </Link>
          </Reveal>
        ))}
      </div>

      <Reveal delay={200}>
        <p className="mt-8 text-[14px] text-[var(--mkt-muted)]">
          Questions first?{" "}
          <Link
            href="/faq"
            className="underline decoration-[var(--mkt-hair-warm)] underline-offset-4 transition-colors hover:text-[var(--mkt-ink)]"
          >
            Straight answers, no soft ones
          </Link>
          .
        </p>
      </Reveal>
    </Section>
  );
}

export default Explore;
