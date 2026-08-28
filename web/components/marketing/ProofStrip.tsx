"use client";

// The slim evidence band under the hero — and the small piece of shared
// plumbing the two evidence sections read their numbers through.
//
// The fetching, normalisation and the §2 fallback all belong to
// lib/publicApi.ts. What lives here is only the React-side glue: one hook, one
// shared request, one date formatter. ProofStrip and TrackRecord quote the same
// figures, so they must never disagree about them mid-load.

import { fmtIso, pct, usePublicStats } from "@/lib/usePublicStats";
import { Reveal, Section } from "./primitives";

/* SPEC §2, verbatim. Duplicated here rather than linked because the spec asks
   for it "wherever the index appears", and this band is where most visitors
   meet the index — it is above the fold and the record section is not. */
const DISCLOSURE =
  "The decisions index is a paper, equal-weight model of the baskets the club "
  + "voted for, marked at daily closes. It is not any member’s account return: "
  + "it excludes fees, slippage, order minimums and the timing of real fills. "
  + "Past performance does not guarantee future results.";

/* ── The band ──────────────────────────────────────────────────────────────── */

export function ProofStrip() {
  const s = usePublicStats();

  const cells: Array<{
    key: string;
    value: React.ReactNode;
    label: string;
    note: string;
    accent?: boolean;
  }> = [
    {
      key: "club",
      value: pct(s.club_pct),
      label: "Decisions index",
      // The paper label rides in the same visual block as the figure — SPEC §0.
      note: `paper · since ${fmtIso(s.live_since, false)}`,
      accent: true,
    },
    {
      key: "spy",
      value: pct(s.spy_pct),
      label: "S&P 500",
      note: "same window",
    },
    {
      key: "sessions",
      // Also settled, and for a mechanical reason on top of the honesty one.
      // CountUp's tween effect is keyed on `to`, so when the live count lands
      // (any value other than the §2 fallback of 24) the effect re-runs, the
      // observer re-fires on an element that is still on screen, and the tally
      // visibly falls back to ~0 and re-counts. And because the tween is driven
      // by requestAnimationFrame, a page opened in a BACKGROUND tab never ticks
      // it at all — the cell sits on "0 baskets decided" until the tab is
      // focused. Neither is acceptable for a factual count on this page.
      value: String(s.n_sessions),
      label: "Baskets decided",
      note: `~${s.universe_size} names each`,
    },
    {
      key: "cost",
      // Not a CountUp: "$0" is a policy, not a measurement, and watching a
      // price tick up from zero to zero is theatre.
      value: "$0",
      label: "Cost to members",
      // "non-custodial" breaks across the hyphen in a 2-up phone column; the
      // plain phrase says the same thing and stays on one line.
      note: "no fees · no custody",
    },
  ];

  return (
    <Section className="pb-4 pt-6 sm:pt-10">
      <Reveal>
        <dl className="grid grid-cols-2 border-y border-[var(--mkt-hair)] sm:grid-cols-4">
          {cells.map((c, i) => (
            <div
              key={c.key}
              className={[
                // DOM order is dt → dd (valid description list); `order`
                // flips it visually so the figure leads and the caption sits
                // under it, without lying to a screen reader about which is which.
                "flex flex-col px-4 py-7 sm:px-6 sm:py-9",
                // Hairline dividers only between cells, computed per breakpoint
                // rather than with a blanket rule: the grid is 2-up on phones
                // and 4-up above, so "first in row" is a different index in each.
                i % 2 !== 0 ? "border-l border-[var(--mkt-hair)]" : "",
                i % 4 !== 0 ? "sm:border-l" : "sm:border-l-0",
                i >= 2 ? "border-t border-[var(--mkt-hair)]" : "",
                "sm:border-t-0",
              ].join(" ")}
            >
              <dt className="mkt-mono order-2 mt-3 text-[var(--mkt-muted)]">{c.label}</dt>
              <dd
                className="mkt-display tnum order-1 text-[clamp(1.7rem,4.4vw,2.5rem)] leading-none"
                style={{ color: c.accent ? "var(--mkt-green)" : "var(--mkt-ink)" }}
              >
                {c.value}
              </dd>
              <dd className="mkt-mono order-3 mt-1.5 text-[10px] leading-[1.5] text-[var(--mkt-subtle)]">
                {c.note}
              </dd>
            </div>
          ))}
        </dl>
      </Reveal>

      {/* The index appears in this band, so the SPEC §2 disclosure appears in
          it too — same block, reading size, no disclosure hidden behind a link
          or a hover. It is set at --mkt-subtle so it reads as the footnote it
          is; that colour measures 4.8:1 on --mkt-bg, i.e. it still clears AA. */}
      <Reveal delay={80}>
        <p className="mt-5 max-w-[86ch] text-[11.5px] leading-[1.6] text-[var(--mkt-subtle)]">
          {DISCLOSURE}
        </p>
      </Reveal>
    </Section>
  );
}
