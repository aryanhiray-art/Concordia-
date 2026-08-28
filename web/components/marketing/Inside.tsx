// What sits between a vote and an order. Six capabilities, each one a thing the
// backend actually does — the claims map to backend/app/services/{voting,
// executor,alloc,watchdog}.py and the audit_log table in app/db.py. Nothing
// here is a promise about outcomes; every line is present tense, mechanical,
// and checkable against the code.
//
// No "use client": this section is static, and the primitives it composes carry
// their own client boundary.

import { Reveal, Section, SectionHead } from "./primitives";

/* ── Icons ─────────────────────────────────────────────────────────────────
   Hand-drawn rather than pulled from a set, so all six share one 24px box, one
   1.5 stroke and one round cap. They inherit currentColor, which is what lets
   the card tint them on hover without a second colour token. */

function Icon({ children }: { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24" width="24" height="24"
      fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

const IconBasket = (
  <Icon>
    <path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2" />
    <path d="M16 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2" />
    <path d="M9 8.5h6" /><path d="M9 12h6" /><path d="M9 15.5h4" />
  </Icon>
);

const IconVerified = (
  <Icon>
    <path d="M20.4 11.3A8.5 8.5 0 1 1 17.3 5" />
    <path d="M20.8 3.6v4.2h-4.2" />
    <path d="m8.7 12.3 2.4 2.3 4.6-4.9" />
  </Icon>
);

const IconSweep = (
  <Icon>
    <circle cx="12" cy="12" r="1.7" />
    <path d="M8.6 15.4a4.8 4.8 0 0 1 0-6.8" />
    <path d="M15.4 8.6a4.8 4.8 0 0 1 0 6.8" />
    <path d="M5.8 18.2a9 9 0 0 1 0-12.4" />
    <path d="M18.2 5.8a9 9 0 0 1 0 12.4" />
  </Icon>
);

const IconKey = (
  <Icon>
    <circle cx="8.2" cy="8.2" r="3.9" />
    <path d="M11 11 20 20" />
    <path d="m14.6 14.6-2.1 2.1" />
    <path d="m17.3 17.3-2.1 2.1" />
  </Icon>
);

const IconCap = (
  <Icon>
    <path d="M3.8 17a8.2 8.2 0 0 1 16.4 0" />
    <path d="M12 17 15.8 11.6" />
    <path d="M3.8 20.2h16.4" />
  </Icon>
);

const IconLedger = (
  <Icon>
    <path d="M4 5.5h16" /><path d="M4 10h16" /><path d="M4 14.5h9" />
    <path d="M18 12.6v6" />
    <path d="m15.7 16.3 2.3 2.3 2.3-2.3" />
  </Icon>
);

/* ── Items ─────────────────────────────────────────────────────────────────── */

const ITEMS: Array<{ icon: React.ReactNode; title: string; body: string }> = [
  {
    icon: IconBasket,
    title: "Baskets lock before they run",
    body: "Voting closes, the consensus picks are chosen and written down, and only then does any order exist. Every cycle's basket stays on the record afterwards.",
  },
  {
    icon: IconVerified,
    title: "Orders are reviewed, then placed once",
    body: "Each order is submitted for review before it is placed, and carries a reference derived from the session, the member and the symbol — so a retry finds the existing order instead of creating a second one.",
  },
  {
    icon: IconSweep,
    title: "A sweep watches the quiet week",
    body: "A health check runs through the day against the database and alerts on the failures a quiet week hides: a dead broker token, a ballot that never opened, a stuck session, a disarmed executor on a cycle morning.",
  },
  {
    icon: IconKey,
    title: "Live orders need a human to arm them",
    body: "Placing real orders requires the master armed flag as well as live mode, and that flag is set from an authenticated admin action — never by a scheduled job. Disarmed, a run still computes the basket; it just does not place.",
  },
  {
    icon: IconCap,
    title: "Your cap is enforced per account",
    body: "You set how much of your own buying power the club may use. Sizing is clamped to that, to an optional daily dollar cap, and to a ceiling on how many orders a single run may place.",
  },
  {
    icon: IconLedger,
    title: "An append-only audit log",
    body: "Arming, vote closes, executions and alerts are appended to a log with actor, action, target and timestamp. Entries are added, never edited.",
  },
];

// Column spans at lg, one per ITEM, in order. Kept beside the data rather than
// computed, so re-ordering ITEMS is an obvious, visible edit to both.
const SPANS = [
  "lg:col-span-4", "lg:col-span-2",
  "lg:col-span-2", "lg:col-span-4",
  "lg:col-span-4", "lg:col-span-2",
];

export function Inside() {
  return (
    <Section id="inside" className="py-24 sm:py-32">
      <SectionHead
        eyebrow="Inside the club"
        title="What runs underneath."
        lead="A vote only matters if the thing that acts on it is boring, checkable and slow to do anything irreversible. These are the parts that make a vote become an order — and the parts that stop one."
      />

      {/* Bento, not a uniform 3x3. Six equal tiles read as a spec sheet — the
          eye finds no entry point and no ranking. Alternating 4/2 spans on a
          six-column grid gives the row a rhythm and lets the longer bodies sit
          in the wider tiles. Below lg it collapses to two columns and then one,
          where varied spans would only produce ragged gaps. */}
      <ul className="mt-14 grid gap-4 sm:mt-20 sm:grid-cols-2 lg:grid-cols-6">
        {ITEMS.map((it, i) => (
          <Reveal
            as="li"
            key={it.title}
            delay={(i % 2) * 70}
            className={`h-full ${SPANS[i]}`}
          >
            <article className="mkt-card mkt-card-hover flex h-full flex-col p-6 sm:p-7">
              <div className="flex items-start justify-between gap-4">
                <span
                  aria-hidden
                  className="flex h-11 w-11 flex-none items-center justify-center rounded-[13px] border border-[var(--mkt-hair)] bg-[var(--mkt-panel-2)] text-[var(--mkt-green)]"
                >
                  {it.icon}
                </span>
                <span aria-hidden className="mkt-mono text-[10px] text-[var(--mkt-subtle)]">
                  {String(i + 1).padStart(2, "0")}
                </span>
              </div>

              {/* In a bento the row height is set by the tallest tile, so a wide
                  tile with a short body left a dead gap under its text. Pushing
                  the copy to the baseline makes that space read as deliberate
                  rather than as a card that failed to fill. */}
              <h3 className={`mkt-display mkt-h3 text-[var(--mkt-ink)] ${SPANS[i].includes("span-4") ? "mt-auto pt-10" : "mt-6"}`}>{it.title}</h3>
              <p className="mt-3 max-w-[62ch] text-[13.5px] leading-[1.6] text-[var(--mkt-muted)]">{it.body}</p>
            </article>
          </Reveal>
        ))}
      </ul>
    </Section>
  );
}
