// Page foot. Half of this is navigation and half is the disclosure block, and
// the disclosure is the half that matters: this is a public page about a club
// that moves real money in real brokerage accounts, so the "not an adviser /
// you can lose principal" language is set at 13px in the muted ink, not
// hidden at 10px in the subtle one. Small enough to be a footnote, large
// enough that nobody can say it was buried.
//
// Not built on <Section/> because that primitive renders a <section>; the
// container widths below are copied from it verbatim so the footer's gutters
// line up with every section above.

import { Logo } from "@/components/ui";
import { Reveal } from "./primitives";

type Link = { label: string; href: string };

// The anchor ids belong to the section components (units C and D). They are
// listed here in one place so a rename shows up as one diff, not five — and
// each one below is copied from the `<Section id=…>` that actually renders it
// (Why.tsx "why", HowItWorks.tsx "how", Difference.tsx "difference",
// TrackRecord.tsx "record", Inside.tsx "inside", Faq.tsx "faq"). A footer link
// to an id that does not exist is a link that does nothing.
const PRODUCT: Link[] = [
  { label: "How it works", href: "#how" },
  { label: "The difference", href: "#difference" },
  { label: "Track record", href: "#record" },
  { label: "Questions", href: "#faq" },
  { label: "Live demo", href: "https://demo.voteconcordia.com" },
];

// One entry, not two: /join is the sign-in screen AND the invite screen, so
// listing both was a pair of differently-named links to the same page.
const ACCOUNT: Link[] = [
  { label: "Sign in or join", href: "/join" },
];

const LEGAL: Link[] = [
  { label: "Terms", href: "/terms" },
  { label: "Privacy", href: "/privacy" },
];

// A footer link group is navigation, not a document section. Using <h2> here
// put "Product / Account / Legal" into the page's heading outline as siblings
// of the real section headings, which is what a screen-reader user gets when
// they list headings to skim the page. A labelled <nav> landmark is the
// correct affordance and keeps the outline clean.
function Column({ title, links }: { title: string; links: Link[] }) {
  return (
    <nav aria-label={title}>
      <p className="mkt-mono text-[var(--mkt-subtle)]">{title}</p>
      <ul className="mt-4 space-y-2.5">
        {links.map((l) => (
          <li key={l.label}>
            <a
              href={l.href}
              className="text-[14px] text-[var(--mkt-muted)] transition-colors duration-300 hover:text-[var(--mkt-ink)]"
            >
              {l.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function MktFooter() {
  return (
    <footer className="relative border-t border-[var(--mkt-hair)] bg-[var(--mkt-bg-2)]">
      <div className="mx-auto w-full max-w-[1180px] px-5 py-16 sm:px-8 sm:py-20">
        <Reveal>
          <div className="grid gap-12 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.35fr)_repeat(3,minmax(0,0.55fr))] lg:gap-10">
            <div className="max-w-[34ch]">
              <div className="flex items-center gap-2.5">
                <Logo size={30} />
                <span className="font-display text-[20px] font-medium tracking-[-0.03em] text-[var(--mkt-ink)]">
                  concordia
                </span>
              </div>
              <p className="mt-5 text-[14px] leading-[1.65] text-[var(--mkt-muted)]">
                A private investing club. The club votes, your own account
                trades, and nobody holds your money.
              </p>
            </div>

            <Column title="Product" links={PRODUCT} />
            <Column title="Account" links={ACCOUNT} />
            <Column title="Legal" links={LEGAL} />
          </div>
        </Reveal>

        <hr className="mkt-rule my-12" />

        {/* Disclosure. The second paragraph is quoted VERBATIM from the build
            contract (SPEC.md §2) and must not be reworded, trimmed or split —
            it is the label that keeps the performance chart from reading as
            "our returns". */}
        <div className="max-w-[86ch] space-y-4 text-[13px] leading-[1.7] text-[var(--mkt-muted)]">
          <p>
            Concordia is a private investment club. It is not a registered
            investment adviser, a broker-dealer, or a fund, and it never takes
            custody of member money. Nothing on this site is investment advice
            or a recommendation to buy or sell any security. Investing involves
            risk, including the possible loss of principal. Members trade in
            their own brokerage accounts and are solely responsible for their
            own decisions.
          </p>
          <p>
            The decisions index is a paper, equal-weight model of the baskets
            the club voted for, marked at daily closes. It is not any
            member&rsquo;s account return: it excludes fees, slippage, order
            minimums and the timing of real fills. Past performance does not
            guarantee future results.
          </p>
          <p className="text-[var(--mkt-subtle)]">
            Robinhood is a trademark of Robinhood Markets, Inc. Concordia is not
            affiliated with, sponsored by, or endorsed by Robinhood.
          </p>
        </div>

        <div className="mt-12 flex flex-col gap-3 border-t border-[var(--mkt-hair)] pt-7 sm:flex-row sm:items-center sm:justify-between">
          {/* 2026 is hardcoded on purpose: new Date() on the server and on the
              client can straddle midnight (or a timezone) and React throws a
              hydration mismatch on the difference. */}
          <p className="mkt-mono text-[var(--mkt-subtle)]">© 2026 Concordia</p>
          <p className="mkt-mono text-[var(--mkt-subtle)]">Live since June 2026</p>
        </div>
      </div>
    </footer>
  );
}
