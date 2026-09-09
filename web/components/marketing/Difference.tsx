import { Section, SectionHead, Reveal, Glow } from "./primitives";

/* Section 03 — the three-way comparison.
   Two hard constraints shaped this file.

   1. FAIRNESS. The alternatives are not strawmen: a copy-trading app really
      does leave custody with the member, and a real investment club really
      does vote. The rows below say so. Nothing here claims Concordia performs
      better — the difference is structural, and a comparison table is exactly
      where a performance claim would sneak in.

   2. TWO RENDERINGS, ONE DATASET. A 4×4 grid is unreadable under ~768px and
      an overflow-scrolled table is worse (the row label scrolls away from its
      value). So the same ROWS array renders as a real <table> on desktop and
      as stacked per-row <dl> groups on mobile. Only one is in the DOM's
      accessibility tree at a time because `hidden` is display:none. */

type Row = {
  label: string;
  club: string;
  copy: string;
  concordia: string;
};

const COLS = ["Traditional investment club", "Copy-trading app", "Concordia"] as const;

const ROWS: Row[] = [
  {
    label: "Who decides",
    club: "Members vote, usually at a meeting.",
    copy: "One leader you choose to follow.",
    concordia:
      "The club, by weighted vote — capital committed blended with historical accuracy.",
  },
  {
    label: "Who holds the money",
    club: "The club does, jointly, as a partnership.",
    copy: "You do. It stays in your own account.",
    concordia: "You do. Concordia never holds, pools, or transfers it.",
  },
  {
    label: "Where trades happen",
    club: "One shared brokerage account.",
    copy: "Your account, mirroring the leader's trades.",
    concordia: "Your own Robinhood account, through its agentic API.",
  },
  {
    label: "What it costs",
    club: "Dues, plus the accounting and tax filing a partnership requires.",
    copy: "Varies by platform — a subscription, a spread, or a share of profits.",
    concordia: "Free to members.",
  },
];

const CAPTION =
  "How Concordia compares with a traditional investment club and a copy-trading app, across who decides, who holds the money, where trades happen, and what it costs.";

/* The emphasised column. Tinted panel + warmer vertical hairlines, rounded at
   the two ends — the same glass the cards use, so it reads as "ours" without a
   badge shouting it. Row rules stay the neutral hairline so the column does
   not turn into a ladder. */
const OWN_CELL =
  "bg-[var(--mkt-panel-2)] border-x border-x-[color:var(--mkt-hair-warm)]";

const CELL = "align-top px-5 py-6 lg:px-6";
const RULE = "border-b border-b-[color:var(--mkt-hair)]";

export function Difference() {
  return (
    <Section id="difference" className="py-24 sm:py-32 lg:py-40">
      {/* Own negative-z layer — see the note in HowItWorks: Section's inner box
          is `relative z-[2]`, and an absolute z-index:0 child paints ABOVE the
          non-positioned table beside it. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-[1]">
        <Glow className="-bottom-24 right-[-12%]" size={540} tone="green" intensity={0.08} />
      </div>

      <SectionHead
        eyebrow="04 — The difference"
        title="Same market. Different structure."
        lead="Three ways to invest alongside other people. All of them can hold the same stock on the same day. The difference is who decides — and whose account it lands in."
      />

      {/* ── Desktop: a real table ─────────────────────────────────────────── */}
      {/* lg, not md. At 768px the three body columns are ~27% of a 704px
          container minus padding — about 20 characters of measure for four rows
          of prose. The table only becomes comfortable near the 1180px cap, so
          the stacked form (capped below) carries everything under 1024px. */}
      <Reveal className="mt-14 hidden sm:mt-20 lg:block">
        <table className="w-full border-separate border-spacing-0 text-left">
          <caption className="sr-only">{CAPTION}</caption>
          <thead>
            <tr>
              {/* Corner cell: a <td> because it labels neither a row nor a
                  column, and a blank <th> would be announced as one. */}
              <td className="w-[19%] px-1 pb-5">
                <span className="sr-only">Comparison dimension</span>
              </td>
              {COLS.map((c, i) => {
                const own = i === COLS.length - 1;
                return (
                  <th
                    key={c}
                    scope="col"
                    className={`w-[27%] px-5 pb-5 pt-6 align-bottom text-[15px] font-medium tracking-tight lg:px-6 ${
                      own
                        ? `${OWN_CELL} rounded-t-[20px] border-t border-t-[color:var(--mkt-hair-warm)] text-[var(--mkt-ink)]`
                        : "text-[var(--mkt-muted)]"
                    }`}
                  >
                    <span className="flex items-center gap-2.5">
                      {own && (
                        <span aria-hidden className="h-[5px] w-[5px] rotate-45 bg-[var(--mkt-green)]" />
                      )}
                      {c}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r, i) => {
              const last = i === ROWS.length - 1;
              return (
                <tr key={r.label}>
                  <th
                    scope="row"
                    className={`mkt-mono px-1 py-6 align-top text-[var(--mkt-subtle)] ${
                      last ? "" : RULE
                    }`}
                  >
                    {r.label}
                  </th>
                  <td className={`${CELL} text-[15px] leading-[1.6] text-[var(--mkt-muted)] ${last ? "" : RULE}`}>
                    {r.club}
                  </td>
                  <td className={`${CELL} text-[15px] leading-[1.6] text-[var(--mkt-muted)] ${last ? "" : RULE}`}>
                    {r.copy}
                  </td>
                  <td
                    className={`${CELL} ${OWN_CELL} text-[15px] leading-[1.6] text-[var(--mkt-ink)] ${
                      last
                        ? "rounded-b-[20px] border-b border-b-[color:var(--mkt-hair-warm)]"
                        : RULE
                    }`}
                  >
                    {r.concordia}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Reveal>

      {/* ── Mobile: the same rows, stacked ────────────────────────────────── */}
      <div className="mt-12 max-w-[720px] lg:hidden">
        {ROWS.map((r, i) => (
          <Reveal key={r.label} delay={i * 60} className={i === 0 ? "" : "mt-10"}>
            <h3 className="mkt-mono border-t border-[color:var(--mkt-hair)] pt-5 text-[var(--mkt-subtle)]">
              {r.label}
            </h3>
            <dl className="mt-4 space-y-3">
              {([
                [COLS[0], r.club, false],
                [COLS[1], r.copy, false],
                [COLS[2], r.concordia, true],
              ] as const).map(([name, value, own]) => (
                <div
                  key={name}
                  className={
                    own
                      ? "rounded-[16px] border border-[color:var(--mkt-hair-warm)] bg-[var(--mkt-panel-2)] px-4 py-3.5"
                      // transparent border, not none: keeps the text of the
                      // three entries on one left edge.
                      : "border border-transparent px-4 py-3.5"
                  }
                >
                  <dt className="mkt-mono flex items-center gap-2 text-[var(--mkt-subtle)]">
                    {own && <span aria-hidden className="h-[5px] w-[5px] rotate-45 bg-[var(--mkt-green)]" />}
                    {name}
                  </dt>
                  <dd
                    className={`mt-2 text-[14.5px] leading-[1.6] ${
                      own ? "text-[var(--mkt-ink)]" : "text-[var(--mkt-muted)]"
                    }`}
                  >
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </Reveal>
        ))}
      </div>

      <Reveal delay={120}>
        <p className="mt-12 max-w-[640px] text-[14px] leading-relaxed text-[var(--mkt-subtle)] sm:mt-14">
          The difference is structural, not a performance claim. Concordia is a
          private club, not an adviser or a broker: it does not manage money and
          does not recommend anything.
        </p>
      </Reveal>
    </Section>
  );
}

export default Difference;
