// The sceptic's section. Everything here is answered from what the backend
// actually does, not from what the marketing copy wishes it did — the claims
// below were each checked against a source file before being written:
//
//   custody / powers   → backend/app/robinhood/client.py  (read tools + market
//                        buy/sell ONLY; there is no transfer or withdraw tool)
//   untouched holdings → backend/app/services/positions.py ("the rebalance must
//                        only ever touch positions the club itself bought")
//   the two switches   → backend/app/routes/members.py  PUT /me/allocation and
//                        PUT /me/participate, including the 409 freeze window
//   voting power       → backend/app/services/reputation.py. NOTE there are two
//                        live score forms (POWER_FORM legacy=additive, merit=
//                        multiplicative) and legacy is the default, so the copy
//                        below stays deliberately form-agnostic: "both feed one
//                        score, expressed as a share". Do not re-specify it as
//                        "dollars scaled by a record" — that is merit only.
//   broker connection  → backend/app/robinhood/oauth.py (PKCE — no password)
//                        and POST /api/oauth/robinhood/disconnect
//
// If any of those change, this file is wrong and becomes a real liability.
//
// Native <details>/<summary>: it opens, closes, and is keyboard- and
// screen-reader-correct with zero JavaScript, which is exactly what you want
// for the one section a visitor may reach with a broken bundle. The default
// disclosure triangle is suppressed and replaced by a plus that folds into a
// minus, driven purely by the `open` attribute.

import { Section, SectionHead, Reveal, Glow } from "./primitives";

type Item = { q: string; a: React.ReactNode };

const ITEMS: Item[] = [
  {
    q: "Does Concordia ever hold my money?",
    a: (
      <>
        No. There is no pooled fund, no partnership account and no Concordia
        balance — the club is non-custodial by construction. Your cash and your
        shares sit in your own Robinhood account, in your name, the whole time.
        Concordia connects to that account through Robinhood&rsquo;s agentic
        API, and that connection cannot withdraw, transfer or move money out of
        it. Not &ldquo;is not allowed to&rdquo;: the integration has no such
        capability to call.
      </>
    ),
  },
  {
    q: "What can Concordia actually do inside my account?",
    a: (
      <>
        Two things. It reads your balances and positions, and it places market
        buy and sell orders for the names in the basket the club voted for. It
        does that only within the slice of buying power you allocate — you set
        either a percentage or a fixed dollar amount, and there is a hard
        per-member ceiling on top of it. Positions the club did not buy are
        never sold: the rebalance works from the club&rsquo;s own execution
        record, so anything you were already holding is left alone.
      </>
    ),
  },
  {
    q: "Can I lose money?",
    a: (
      <>
        Yes. This is real money in real equities, and you can lose part or all
        of what you allocate. A vote is not a forecast; the club can be wrong,
        and being wrong together is still being wrong. Concordia guarantees
        nothing, promises no return, and its past record does not tell you what
        the next basket will do. Allocate only what you can afford to see fall.
      </>
    ),
  },
  {
    q: "What if I disagree with the basket the club picked?",
    a: (
      <>
        You control whether you participate and how much you commit — you do not
        control what wins a vote. That is the point of a club. If you don&rsquo;t
        want the current direction there are two different exits, and they do
        different things. Switch participation off and the club skips your
        account entirely from the next cycle: it places no further orders, and
        the shares it already bought for you simply stay where they are, yours
        to sell whenever you like. Set your allocation to zero instead and the
        next rebalance unwinds the positions the club bought for you — it sells
        them back out rather than leaving them stranded. Either way, anything
        you bought yourself is never touched. One honest caveat: both switches
        lock during the short window between a ballot closing and the basket
        executing, so that nobody can vote at full weight, watch the tally, and
        then defund the result.
      </>
    ),
  },
  {
    q: "How is voting power calculated?",
    a: (
      <>
        It blends two things: the capital you have committed, and how accurate
        your past picks turned out to be. Both feed one score, and your voting
        power is that score as a share of the club&rsquo;s total. So a large
        balance alone does not buy the club, and a member who is repeatedly
        right gains say over time.
        Accuracy is scored from closing prices in the cycle after each vote, and
        power is recomputed on a schedule rather than in reaction to any one
        trade. We describe the mechanism; we don&rsquo;t publish the formula.
      </>
    ),
  },
  {
    q: "What does it cost?",
    a: (
      <>
        Nothing. Concordia charges members no fee, no subscription and no share
        of profits. Whatever your broker charges for its own execution is
        between you and your broker.
      </>
    ),
  },
  {
    q: "Which broker do I need?",
    a: (
      <>
        Robinhood, today — specifically an account with agentic trading enabled.
        That API is what makes a club vote possible without pooling anyone&rsquo;s
        money, and it is why Concordia could not have existed a year ago. You
        authorise the connection on Robinhood&rsquo;s own consent screen;
        Concordia never sees or stores your Robinhood password, and you can
        disconnect from either side at any time.
      </>
    ),
  },
  {
    q: "Is any of this financial advice?",
    a: (
      <>
        No. Concordia is a private investing club — not a registered investment
        adviser, not a broker-dealer, not a fund. Nothing on this site and
        nothing inside the club is a recommendation or personalised advice, and
        no one here is acting as your adviser. Members vote, each member&rsquo;s
        own account executes, and each member is responsible for their own
        decisions.
      </>
    ),
  },
];

export function Faq() {
  return (
    // NO `overflow-hidden` on this section, deliberately. It would make the
    // section a scroll container, and `position: sticky` resolves against the
    // NEAREST scrolling ancestor — so an overflow-hidden wrapper silently kills
    // the sticky head below (measured: the head scrolled clean off instead of
    // pinning). The glow bleeding past the section edge is already contained by
    // `html, body { overflow-x: hidden }` in globals.css, so nothing scrolls
    // sideways without it.
    <Section id="faq" className="relative py-24 sm:py-32">
      <Glow className="-left-[18%] top-[22%]" tone="teal" intensity={0.1} size={560} />

      {/* `relative` is load-bearing: .mkt-glow is position:absolute z-index:0,
          and a positioned z-0 element paints ABOVE non-positioned in-flow
          content. Without this the wash would sit on top of the questions. */}
      <div className="relative grid gap-12 lg:grid-cols-[minmax(0,0.78fr)_minmax(0,1.22fr)] lg:gap-16">
        {/* Two-column editorial split: head left, answers right. The head is
            sticky where the browser allows it — see the note in the review
            findings about globals.css `body { overflow-x: hidden }`, which is
            a second, page-wide sticky blocker that cannot be fixed from here. */}
        <div className="lg:sticky lg:top-28 lg:self-start">
          <SectionHead
            eyebrow="Questions"
            title={<>The things worth asking before you connect an account.</>}
            lead="Short answers, and no soft ones. Where the honest answer is uncomfortable, it is still the answer."
          />
          {/* Label and links on separate lines. As one mono sentence it wrapped
              mid-phrase at 360px and orphaned the "·" onto its own line —
              11px uppercase at 0.16em tracking is a label typeface, not a
              running-text one. */}
          <Reveal delay={180}>
            <div className="mt-9">
              <p className="mkt-mono text-[var(--mkt-subtle)]">Same ground, in legal language</p>
              <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13.5px] text-[var(--mkt-muted)]">
                <a href="/terms" className="underline decoration-[var(--mkt-hair-warm)] underline-offset-4 transition-colors hover:text-[var(--mkt-ink)]">
                  Terms
                </a>
                <span aria-hidden className="text-[var(--mkt-subtle)]">·</span>
                <a href="/privacy" className="underline decoration-[var(--mkt-hair-warm)] underline-offset-4 transition-colors hover:text-[var(--mkt-ink)]">
                  Privacy
                </a>
              </p>
            </div>
          </Reveal>
        </div>

        <ul className="border-b border-[var(--mkt-hair)]">
          {ITEMS.map((item, i) => (
            <Reveal as="li" key={item.q} delay={Math.min(i, 4) * 55}>
              <details className="group border-t border-[var(--mkt-hair)]">
                {/* <summary>'s content model is phrasing content OR a SINGLE
                    heading element — not a mix. The heading is therefore the
                    sole child and carries the row layout; the ordinal and the
                    icon live inside it. */}
                <summary className="block cursor-pointer list-none py-5 sm:py-6 [&::-webkit-details-marker]:hidden">
                  {/* Deliberately NOT .mkt-display: marketing.css is imported
                      after Tailwind's utilities, so a same-specificity utility
                      loses to it and the class's 0.96 line-height would clamp a
                      wrapping question. Compose the same look from tokens. */}
                  <h3 className="flex items-start gap-4 font-display text-[1.125rem] font-medium leading-[1.35] tracking-[-0.02em] text-[var(--mkt-ink)] sm:gap-6 sm:text-[1.3rem]">
                    {/* Decorative ordinal. aria-hidden, or the heading (and the
                        summary's accessible name) reads "01Does Concordia ever
                        hold my money?" with no separator. */}
                    <span aria-hidden className="mkt-mono mt-[7px] flex-none tabular-nums text-[var(--mkt-subtle)] transition-colors duration-300 group-open:text-[var(--mkt-green)]">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="flex-1">{item.q}</span>
                    {/* Plus → minus: the vertical bar collapses on open. Two 1px
                        rules rather than an icon font, so it inherits ink colour
                        and stays crisp at any zoom. */}
                    <span
                      aria-hidden
                      className="relative mt-1 h-[22px] w-[22px] flex-none rounded-full border border-[var(--mkt-hair)] text-[var(--mkt-muted)] transition-colors duration-300 group-hover:border-[var(--mkt-hair-warm)] group-hover:text-[var(--mkt-ink)]"
                    >
                      <span className="absolute left-1/2 top-1/2 h-px w-[9px] -translate-x-1/2 -translate-y-1/2 bg-current" />
                      <span className="absolute left-1/2 top-1/2 h-[9px] w-px -translate-x-1/2 -translate-y-1/2 bg-current transition-transform duration-300 ease-[var(--mkt-ease)] group-open:scale-y-0 motion-reduce:transition-none" />
                    </span>
                  </h3>
                </summary>
                {/* Indented to the question's text column on wide screens, flush
                    left on phones where 2.6rem of dead gutter costs real width. */}
                <div className="mkt-lead max-w-[62ch] pb-7 pr-8 sm:pl-[2.6rem]">
                  {item.a}
                </div>
              </details>
            </Reveal>
          ))}
        </ul>
      </div>
    </Section>
  );
}
