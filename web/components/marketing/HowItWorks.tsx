import { Section, SectionHead, Reveal, Glow } from "./primitives";

/* The steps section — the spine of /how-it-works (since the five-page split).
   The three steps are ONE list on a single continuous rail, not three cards:
   the whole point of the product is that step 03 is a consequence of 01 and
   02, and disconnected cards would say the opposite. The rail is just the
   left border of each <li>, so the segments stack into one unbroken line with
   no absolutely-positioned connector to keep in sync — and it collapses to the
   same shape at 360px, where three cards would have to be re-thought. */

type Step = {
  n: string;
  kicker: string;
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    n: "01",
    kicker: "Connect",
    title: "Connect your own brokerage account.",
    body:
      // Step 01 ends at "same login": the custody clause that used to follow
      // ("no money moved") would be this page's SECOND custody line — step 03
      // holds the page's one allowed slot (SPEC §4).
      "Link your Robinhood account through Robinhood's agentic API, and set the " +
      "ceiling — how much of your buying power the club may use. Everything above " +
      "that line stays yours to run as you always have. Same account, same login.",
  },
  {
    n: "02",
    kicker: "Vote",
    title: "Vote on the basket.",
    body:
      // Fresh wording, not home's: the Engine section and the hero already
      // say "one-member-one-vote / whoever-has-the-most-money" and "being
      // right earns more say" — the primary click path lands here next, so
      // this paragraph explains the same SPEC §3 mechanism in its own words.
      "Each cycle the club votes across the S&P 500. The names with the most " +
      "weighted support — roughly twenty — become the basket. Your weight " +
      "blends two things: the capital you've put behind the club, and the " +
      "record of picks the closes proved right. Keep calling it well and your " +
      "ballot carries further.",
  },
  {
    n: "03",
    kicker: "Execute",
    title: "The basket executes in your account.",
    body:
      "One decision for the whole club, sized to each member's own settings. The " +
      "shares land in your account, in your name. Concordia never holds, pools, " +
      "or transfers money — there's nothing to withdraw, because nothing ever " +
      "left.",
  },
];

/* Cadence per SPEC §3 (current: every trading day). Kept as a quiet strip
   rather than a fourth step: it is a detail you check, not a thing you do.
   Times only, no weekday names — the cadence has flipped twice in a month and
   day-of-week copy goes stale the moment it does. */
const CADENCE: { when: string; what: string }[] = [
  { when: "Overnight", what: "Ballot opens" },
  { when: "09:15 ET", what: "Voting closes" },
  { when: "10:15 ET", what: "Basket executes" },
];

export function HowItWorks() {
  return (
    <Section id="how" className="py-24 sm:py-32 lg:py-40">
      {/* The glow must sit in its OWN negative-z layer, not loose in the
          section. Section's inner box is `relative z-[2]`, and a
          position:absolute z-index:0 child of a stacking context paints ABOVE
          its in-flow, non-positioned siblings — so a bare <Glow/> washes teal
          over the copy instead of behind it. (Setting -z-[1] on the Glow
          itself would not work: marketing.css loads after Tailwind, so
          `.mkt-glow { z-index: 0 }` wins the tie.) Same wrapper as Hero. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-[1]">
        <Glow className="top-1/3 left-[-18%]" size={560} tone="green" intensity={0.09} />
      </div>

      {/* Retitled for the /how-it-works page: the old title ("...Trade in your
          own account.") duplicated both the page hero above it and the custody
          framing this page already spends in step 03 — its single allowed
          custody line (SPEC §4). */}
      <SectionHead
        eyebrow="The steps"
        title="Three steps, one loop."
        lead="Each step is a consequence of the one before — one unbroken line."
      />

      <ol className="mt-14 sm:mt-20">
        {STEPS.map((s, i) => {
          const last = i === STEPS.length - 1;
          return (
            <Reveal
              as="li"
              key={s.n}
              delay={i * 90}
              // The rail stops at step 03's marker: the last item keeps the
              // border box for alignment but draws nothing.
              className={`relative border-l pl-6 sm:pl-10 ${
                last ? "border-l-transparent" : "border-l-[color:var(--mkt-hair)] pb-12 sm:pb-16"
              }`}
            >
              <span
                aria-hidden
                className="absolute -left-[3px] top-[11px] h-[7px] w-[7px] rotate-45 bg-[var(--mkt-green)]"
              />
              {/* A fixed numeral column, not a fraction: on a 1180px canvas a
                  fractional split leaves ~300px of void beside a 60px numeral
                  and the eye has to travel to find the sentence. */}
              <div className="grid gap-y-4 sm:grid-cols-[110px_minmax(0,1fr)] sm:gap-x-8 lg:grid-cols-[150px_minmax(0,1fr)] lg:gap-x-12">
                <div>
                  <span className="mkt-num block">{s.n}</span>
                  <span className="mkt-mono mt-3 block text-[var(--mkt-subtle)]">{s.kicker}</span>
                </div>
                <div>
                  <h3 className="mkt-display mkt-h3 text-[var(--mkt-ink)]">{s.title}</h3>
                  <p className="mkt-lead mt-4 max-w-[62ch]">{s.body}</p>
                </div>
              </div>
            </Reveal>
          );
        })}
      </ol>

      <Reveal delay={90} className="mt-14 sm:mt-20">
        <div className="mkt-card p-6 sm:p-8">
          <div className="mkt-mono text-[var(--mkt-subtle)]">Cadence — every trading day</div>
          <ol className="mt-6 grid gap-y-5 sm:grid-cols-3 sm:gap-x-8">
            {CADENCE.map((c) => (
              <li
                key={c.when}
                className="border-t border-[color:var(--mkt-hair)] pt-4 sm:border-t-0 sm:border-l sm:border-l-[color:var(--mkt-hair)] sm:pl-5 sm:pt-0"
              >
                <div className="tnum font-display text-[16px] font-medium tracking-tight text-[var(--mkt-ink)]">
                  {c.when}
                </div>
                <div className="mt-1.5 text-[13.5px] text-[var(--mkt-muted)]">{c.what}</div>
              </li>
            ))}
          </ol>
          <p className="mt-7 text-[13px] leading-relaxed text-[var(--mkt-subtle)]">
            Deferred buys top up the next trading day. Voting power is recomputed on its own schedule.
          </p>
        </div>
      </Reveal>
    </Section>
  );
}

export default HowItWorks;
