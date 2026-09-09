# Concordia marketing site — build contract

Every agent building a section reads this first. It pins the facts, the voice,
and the API so sections written in parallel stay coherent and, more important,
stay **true**. This is a page about real money; an unverifiable claim is a bug.

## 0. Non-negotiables

- **Never invent a number.** Only the figures in §2 may appear. If a section
  wants a stat that is not listed, it does not get a stat.
- **Never imply advice or registration.** Concordia is a *private club*, not an
  adviser or broker. No "returns you can expect", no "we manage", no
  "recommended". The club decides; the member's own account executes.
- **Never show member counts or names.** The club currently includes test bots;
  publishing a headcount would overstate it.
- The performance series is a **paper decisions index**, never "our returns".
  Any place it appears must carry that label within the same visual block.

## 1. Voice

Short declaratives, second person, present tense. The register is the one
Robinhood, Public, dub and Autopilot share (surveyed 2026-09-09): plain
sentences that survive being read aloud, confidence carried by facts rather
than adjectives, and the occasional dry aside. Periods over semicolons.
Contractions are welcome; "whom" is not. Where a sentence grows a subordinate
clause, split it.

Still banned: "revolutionary", "seamless", "empowering", "unlock",
"supercharge" — and equally banned in the other direction: slang, emoji, and
anything that reads as a costume ("fr", "no cap", "level up"). Gen Z reads
plain confidence as native and forced slang as marketing. Prefer the concrete
noun to the abstract one ("your Robinhood account", not "your financial
future").

Good: *"Vote together. More right, more power."*
Good: *"Nothing. No fee, no subscription, no share of profits."*
Bad: *"Concordia empowers investors to unlock collective intelligence."*
Bad: *"your portfolio is about to level up fr"*

## 2. Facts (the ONLY publishable numbers)

| Fact | Value | Source |
|---|---|---|
| Live since | 17 June 2026 | first decided session |
| Decided baskets | 24 (through 2026-08-24) | `performance.decided_cycles()` |
| Decisions index | **+6.85%** | verified: independent recomputation over all 24 locked baskets, every price spot-checked against Robinhood |
| SPY buy & hold, same window | **+3.04%** | same computation |
| Universe | ~20 S&P 500 names per cycle | |
| Cost to members | free | |
| Custody | none — Concordia never holds member money | non-custodial by construction |

**Prefer live values from `/api/public/stats` over the table above.** The table
is the fallback when the API is unreachable, and the sanity check on it.

The top-bar tape is the one other place numbers appear: live market quotes
(Alpaca market data via `/api/public/tape`, cached server-side) for a fixed
editorial list of S&P 500 mega-caps hardcoded in `backend/app/routes/public.py`.
That list is never read from any club table — it says nothing about what the
club holds — and when the feed is down, empty, or stale past the backend's
ceiling, the bar renders the claims strip instead of a price nobody just
fetched.

Required disclosure, verbatim, wherever the index appears and in the footer:

> The decisions index is a paper, equal-weight model of the baskets the club
> voted for, marked at daily closes. It is not any member's account return: it
> excludes fees, slippage, order minimums and the timing of real fills. Past
> performance does not guarantee future results.

## 3. What Concordia actually is (for copy)

An invite-only investing club. Members vote each cycle on which S&P 500 names
the club should hold. The winning basket executes **in each member's own
Robinhood account** through Robinhood's agentic API — money is never pooled,
never transferred, never held by Concordia. Each member sets how much of their
own buying power the club may use; the rest of the account is untouched.

Voting power is not one-member-one-vote and not pure plutocracy: it blends
**capital committed** with **historical accuracy**, so a member who is right
repeatedly gains say over time.

Cadence (current: every trading day): the ballot closes 09:15 ET on each trade
day, the basket executes 10:15 ET, and deferred buys top up the next trading
day. Voting power is recomputed on its own schedule. Copy must never hardcode
weekday names — the cadence has changed twice; state times + "every trading
day" and defer to the app's schedule.

### The positioning (why this is new)

Three families of predecessors, and what each got wrong:

1. **Investment clubs** (the NAIC tradition) vote — but pool money into a
   partnership. That paperwork is what killed them.
2. **Copy-trading apps** trade your own account — but you follow one leader,
   with no say.
3. **Thematic basket platforms** (Motif) let you buy a basket — but there was
   no club and no vote, and the economics never worked.

Concordia is the first with all three at once: **a vote, your own account, and
nobody holding the money.** Broker agent APIs only made that possible in 2026.

One more publishable feature fact (verified 2026-09-09 against
`backend/app/routes/voting.py` and `services/voting.symbol_theses`): a member
may pin an optional short thesis to a pick, and the ballot shows the latest
few human rationales beside the tally. This is what grounds "argue" and
"win the vote" language — copy may state it, but never as a research tool or
a chat (it is a one-line case per pick, nothing more).

## 4. Site structure (five pages since 2026-09-09)

The one-page site split into five. THE MAIN POINT SITE-WIDE IS COLLECTIVE
WISDOM — the club measures who was right and gives them more say. Custody is a
BENEFIT, never the lead: at most one benefit-framed custody line per page
(structural table rows and the footer's legal disclosure don't count against
it). Shared chrome (ticker, nav, footer) lives in MktShell; ClosingCTA closes
every page, so its copy is shared state — a custody line there would hit every
page at once. Teaser cards never carry index figures: the disclosure doesn't
travel to a teaser, so no number may either.

- `/` — Hero (wisdom tagline) · ProofStrip · Engine (accuracy-weighted power,
  the one custom section) · ProductPreview · Explore (subpage teaser cards,
  each reusing its subpage's H1 verbatim) · ClosingCTA
- `/how-it-works` — PageHero · HowItWorks (3 steps + cadence) · Inside · ClosingCTA
- `/difference` — PageHero · Why (the problem) · Difference (comparison) · ClosingCTA
- `/record` — PageHero · TrackRecord (chart + verbatim disclosure) · ClosingCTA
- `/faq` — Faq (its own SectionHead is the page h1) · ClosingCTA

Routes live in `web/app/<page>/page.tsx` (server, metadata via
`seo.pageMetadata`) rendering `components/marketing/pages/*` (client, wrapped
in MktShell). AppShell renders these paths bare for anyone on the apex host —
keep `MKT_PAGE_PATHS` in seo.ts in step with nav, footer, and sitemap.

## 5. API contract (built by [A]; assume it exists)

`GET /api/public/stats` → 200, **no auth**, cached:

```json
{
  "live_since": "2026-06-17",
  "n_sessions": 24,
  "club_pct": 6.85,
  "spy_pct": 3.04,
  "points": [{ "d": "2026-06-17", "club": 100.0, "spy": 100.0 }],
  "universe_size": 20
}
```

`POST /api/public/waitlist` `{ "email": "..." }` → `{ "ok": true }`;
409 `{ "detail": "already" }` if known. Rate-limited. Never reveals membership.

Frontend helpers live in `web/lib/publicApi.ts` (built by [A]). **Every section
must render correctly when the fetch fails** — fall back to §2's table, never
to a spinner that never resolves and never to a zero.

## 6. Technical rules

- Tokens/classes from `app/marketing.css`; primitives from
  `components/marketing/primitives.tsx` (`Section`, `SectionHead`, `Reveal`,
  `Eyebrow`, `Glow`, `CTA`, `Tag`, `Marquee`, `CountUp`, `RotatingWord`).
  **Do not invent a parallel set of tokens or re-declare colours inline.**
- Tailwind for layout/spacing; `marketing.css` for anything themed. Reference
  colours as `var(--mkt-*)`, never a raw hex in a component.
- **No `position: fixed` decorative layers** — a fixed tint is pinned to the
  viewport and content scrolls through it (this bug cost the owner four
  rounds). Use `<Glow/>`, which is absolute inside its section.
- Mobile-first. Every section must be correct at 360px wide. Nothing may cause
  horizontal page scroll; wide content scrolls inside its own container.
- Respect `prefers-reduced-motion` (the primitives already do — do not add raw
  CSS animations that bypass it).
- Semantic headings in order, real `<a>`/`<button>`, alt text on meaningful
  imagery, `aria-hidden` on decoration.
- `"use client"` only where a hook is actually used.
- TypeScript strict: no `any`, no unused imports. `npx tsc --noEmit` must pass.
