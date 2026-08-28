import { Section, SectionHead, Reveal, Glow } from "./primitives";

/* Section 01 — the problem.
   Deliberately short. The temptation on a page like this is to prove the
   thesis with six cards; the reference sites make a bigger argument in a third
   of the space and read as more certain for it. So: one heading, three beats,
   one statement that lands into "how it works". Nothing else.

   Every claim here is structural (what a predecessor *is*), never comparative
   performance — see SPEC §0. No dates are invented: the only year named is
   2026, which SPEC §3 attributes to broker agent APIs. */

type Beat = { kicker: string; title: string; body: string };

const BEATS: Beat[] = [
  {
    kicker: "The club",
    title: "Clubs voted — then pooled the money.",
    body:
      "The investment club got the hard part right: a group of people arguing " +
      "their way to a single decision. Then it needed a partnership, one shared " +
      "brokerage account, a treasurer, and a tax form for every member. The " +
      "paperwork is what killed them, not the idea.",
  },
  {
    kicker: "The feed",
    title: "Copy-trading kept your account — and dropped the vote.",
    body:
      "Following a leader fixed custody: the money never leaves your own " +
      "account. But you are mirroring one person's conviction. There is nothing " +
      "to argue for, nothing to be right about, and no vote to win.",
  },
  {
    kicker: "What changed",
    title: "Then brokers opened the account itself.",
    body:
      "Programmatic agent access to an individual brokerage account arrived in " +
      "2026. Before that, a group could decide together or trade in its members' " +
      "own accounts. It could not do both.",
  },
];

export function Why() {
  return (
    <Section id="why" className="py-24 sm:py-32 lg:py-40">
      {/* Own negative-z layer — see the note in HowItWorks: Section's inner box
          is `relative z-[2]`, and an absolute z-index:0 child paints ABOVE the
          non-positioned copy beside it. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-[1]">
        <Glow className="-top-32 right-[-14%]" size={520} tone="teal" intensity={0.1} />
      </div>

      <SectionHead
        eyebrow="01 — The problem"
        title="Investing went social everywhere except the one place it costs money."
        lead={
          "The research is collective. The theses are collective. The screenshots, " +
          "the group chat at 9:29, the three-week argument about one ticker — all " +
          "collective. Then everybody closes the tab and places their own order, alone."
        }
      />

      {/* Three columns on desktop, three stacked blocks on mobile. A left rule
          on every column (rather than boxes) keeps this reading as an editorial
          grid instead of a feature list. */}
      {/* Columns at md, not sm: at 640px a three-way split leaves ~147px of text
          measure per column, which sets a 230-character paragraph as ~12 lines
          of ~20 characters. Stacked reads better until there is room for it —
          and md is the same breakpoint the comparison table switches on. */}
      <div className="mt-14 grid gap-y-9 sm:mt-20 md:grid-cols-3 md:gap-x-8 lg:gap-x-12">
        {BEATS.map((b, i) => (
          <Reveal
            key={b.kicker}
            delay={i * 90}
            className="border-t border-[color:var(--mkt-hair)] pt-6 md:border-t-0 md:border-l md:border-l-[color:var(--mkt-hair)] md:pl-6 md:pt-0 lg:pl-8"
          >
            <div className="mkt-mono text-[var(--mkt-subtle)]">{b.kicker}</div>
            <h3 className="mkt-display mkt-h3 mt-4 text-[var(--mkt-ink)]">{b.title}</h3>
            {/* Not .mkt-lead: marketing.css loads after Tailwind's utilities,
                so a size override on that class would silently lose. Column
                copy wants to sit a step below the section lead anyway. */}
            <p className="mt-3.5 text-[15px] leading-[1.68] text-[var(--mkt-muted)]">{b.body}</p>
          </Reveal>
        ))}
      </div>

      {/* The statement the section exists to deliver. One accent hairline, no
          quotation marks — the size is the emphasis. */}
      <Reveal delay={120} className="mt-20 sm:mt-28">
        <div className="max-w-[900px] border-l border-[color:var(--mkt-green)] pl-6 sm:pl-9">
          <p className="mkt-display mkt-h2 text-[var(--mkt-ink)]">
            <span className="block">A vote.</span>
            <span className="block">Your own account.</span>
            <span className="block">Nobody holding the money.</span>
          </p>
          <p className="mkt-lead mt-6 max-w-[560px]">
            {/* SPEC §3: "the first with all three at once". Deliberately not
                "any two of these existed" — that is a tidier sentence and a
                claim nobody has checked. */}
            Every predecessor had some of these. None had all three at once —
            that needed a broker to open the account itself to an agent, which
            happened in 2026.
          </p>
        </div>
      </Reveal>
    </Section>
  );
}

export default Why;
