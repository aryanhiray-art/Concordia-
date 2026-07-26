# Concordia — MVP → Pre-Seed Pathway

*v1 · 2026-07-25 · synthesized from a full repo product-readiness audit, July-2026 pre-seed market research, and comparable-company/regulatory research. Companion to `ROADMAP_QUANT_PIPELINE.md` (forge) and `infra/MIGRATION.md` (cutover). Sources at bottom.*

---

## 0. TL;DR

Concordia's **core is credible** — months of real-money execution, fail-closed safety rails, two-key arming, 116 passing tests, email-verified auth, legal docs, demo mode. What's missing is everything **around** the product: a way to capture strangers (no landing page, no waitlist, one shared invite code), eyes on it (zero analytics/error tracking/alerting), operator self-sufficiency (admin lives on the old forge box), and a metrics story (the schema already supports one; nothing aggregates it).

**~2–3 focused weeks closes the product gap. ~4–6 more weeks builds the traction story. Raise conversations start in September.**

Three decisions this document takes a position on:

1. **Target YC Winter 2027** (decision made 2026-07-26: skipping the Fall batch). Expected deadline late Oct–mid Nov 2026 (W2025's was Nov 12; exact date unannounced — watch ycombinator.com/apply), batch Jan–Mar 2027. The upside of waiting: the application gets written on top of 8+ weeks of real traction data from Phase 3 instead of a scramble — a materially stronger app. The discipline it demands: Phases 1–3 must actually produce those numbers by mid-October.
2. **Treat RIA registration as the plan, not the risk.** Every funded comparable that drives trades in members' own accounts registered (Dub, Autopilot). "We're free" does not protect — the SEC construes compensation broadly. Interim posture: small, invite-only, free, framed as a private club, blessed by one securities-counsel consult. Pitch line: *"Autopilot's path is our path; registration is a planned use of funds."*
3. **Derisk the Robinhood dependency before pitching.** RH's agentic API (public since May 2026) is explicitly single-user — no stated allowance for a third party orchestrating many members' accounts. Every serious investor will find this. The answer is a formalized broker interface (the seam already exists — DemoClient proves it) plus a working Alpaca spike, whose entire $435M-funded strategy is agent-first, orchestrator-friendly execution.

---

## 1. Where we are (honest inventory)

**Strengths to lead every demo with:**
- Live club, real money, real members, months of daily cycles; decisions-vs-SPY tracked
- Fail-closed prod boot; Fernet token vault with key rotation; dry-run + ARMED two-key execution with kill switch and human-quorum floor
- Auth stack: email verification (Resend), Google sign-in, self-serve password reset, typed-DELETE self-service account deletion with anonymized books, consents ledger
- Guided onboarding wizard; DEMO_MODE stage-prop broker (is_live force-false)
- WAL-safe daily backups (restore runbook exists); 116 backend tests
- Infra costs ~€75/year total. Capital efficiency is a pitch slide, not a footnote.

**Gap clusters (from the audit — full task list in §13):**
1. **Growth mechanics** — one shared invite code; a stranger without it hits a dead end and is lost; no landing page, no waitlist, no referral codes, no public demo entry
2. **Observability** — no analytics, no error tracking, no uptime/job alerting (a real-money 9:31 ET automation with no pager), no KPI aggregation
3. **Operator surface** — admin UI only on the old forge box; no member removal, no ownership transfer, no audit-log viewer; config changes require SSH
4. **Polish debt** — bots can still carry up to 90% of the executable vote (config default), `daily_cap_usd` is displayed but unenforced, docs contradict the code, no CI, thin mobile coverage, the RH connect paste-back is the worst 90 seconds of onboarding

---

## 2. What "MVP" means for this raise (definition of done)

Not "more features." The MVP is done when all seven are true:

- [ ] A stranger can be sent **voteconcordia.com** and understand the product in 30 seconds (landing page), try it in 2 minutes (public demo instance), and leave an email if they want in (waitlist)
- [ ] A member's phone is a first-class client (mobile QA'd; the 8am vote is a phone habit)
- [ ] The owner can run the entire club from the product (admin UI: members, arm/halt, invites, audit trail) with zero SSH
- [ ] Every signup, vote, execution, and error is **measured** — a KPI dashboard an investor could be shown live
- [ ] Nothing embarrassing under poking: bots out of the executable tally, no dead settings, docs match code, CI green
- [ ] The club survives the operator sleeping: uptime alerts, job-failure alerts, tested restore
- [ ] The two killer questions have demonstrated answers: *"What if Robinhood cuts you off?"* (broker interface + Alpaca spike) and *"Aren't you an unregistered adviser?"* (counsel-blessed posture + registration plan)

---

## 3. Track 0 — YC Winter 2027 application (target: submission-ready by mid-October)

*Decision 2026-07-26: Fall 2026 (was due Jul 27) is intentionally skipped. Winter 2027 expected deadline late Oct–mid Nov 2026 (unannounced — check ycombinator.com/apply monthly; W2025's deadline was Nov 12), batch Jan–Mar 2027 in SF. Standard deal unchanged: **$500k** ($125k for 7% + $375k uncapped MFN SAFE), non-negotiable, funds on acceptance.*

The Winter timeline turns the application from a scramble into a harvest — it gets written **on top of Phase 3's traction data**. That also makes Phase 3's targets (§6, 3.5) the de-facto YC gate.

- [ ] **Now:** if currently enrolled as a student, read YC's **Early Decision for Students** program (ycombinator.com/early-decision — apply early, decision early) and check eligibility/deadlines; it may beat the general track
- [ ] **Sep 1:** set a calendar check on ycombinator.com/apply for the official W27 deadline announcement
- [ ] **By Oct 1:** decide and paper Aryan's status (co-founder vs collaborator) — the app asks, and changing the answer later is costly
- [ ] **By Oct 10:** draft written answers from the weekly KPI snapshot doc (§6, 3.4). Wedge framing (see §11): *"Investment clubs died because of paperwork. We rebuilt them on top of agentic brokerage APIs — one ballot a day, executed in every member's own account. Non-custodial, capital-efficient, and the first product native to the agent-API rails Robinhood and Alpaca just shipped."*
- [ ] **By Oct 10:** 1-minute founder video (phone is fine; energy > production); demo footage from the public demo instance (2.4)
- [ ] **Submit within 48h of the deadline announcement, not at the wire**
- [ ] Honesty notes: incorporation status by then (Phase 4 should be done — a plus vs the Fall scramble), regulatory plan (one sentence, confident, not defensive)

*YC 2026 context: fintech is a growth category; RFS themes lean "AI into real-world systems (finance…)" — the agentic-execution angle is on-thesis. Position precisely: the AI is in the rails and the research bots (non-binding), not "AI picks your stocks" — see the Delphia/Global Predictions AI-washing enforcement in §12.*

---

## 4. Phase 1 — Lock the foundation (week of Jul 28)

*Goal: the new box is the only box, nothing can silently fail, nothing embarrassing remains armed.*

| # | Task | Effort | Notes |
|---|------|--------|-------|
| 1.1 | **Cutover** old VPS → voteconcordia.com per `infra/MIGRATION.md` (env **merge**, not copy — keeps Google/Resend/new invite code) | ½ day | Mon ~10 ET as planned |
| 1.2 | **Bots out of the executable consensus** — `BOT_TALLY_SHARE_MAX→0` or shadow-league flag (schema's `is_bot` already supports it); bots stay visible as research signal | hours | **Hard gate before anything public.** "AI bots can control 90% of the vote" does not survive diligence |
| 1.3 | Uptime monitoring on `/health` (healthchecks.io/UptimeRobot) + heartbeat pings from execute/backup timers + Discord webhook on verify-FAIL and backup-ship failure | hours | Real-money automation needs a pager |
| 1.4 | Sentry (backend + web) + a Next.js `global-error.tsx` boundary | hours | The raw "Application error" screen is a known failure mode |
| 1.5 | Off-box backup shipping configured + **one full restore drill** on the new box; Fernet-key out-of-band backup verified | hours | MIGRATION step 15 left this open |
| 1.6 | Enforce `daily_cap_usd` at the executor, or remove it from schema + docs | hours | A safety setting that does nothing is a demo landmine |
| 1.7 | `/health` public response reduced to `{ok:true}` (stop leaking `armed`/mode) | minutes | |
| 1.8 | GitHub Actions CI: pytest + tsc + next build on push | hours | Cheap credibility, needed with a collaborator |
| 1.9 | Docs sweep: ARCHITECTURE/README/DEPLOY match reality (top_n, auth stack, S&P universe, standalone topology) | ½ day | Tech diligence reads docs first |

---

## 5. Phase 2 — The public face (Jul 28 – Aug 8)

*Goal: a URL you can put in a YC interview, a tweet, or an investor email.*

| # | Task | Effort | Notes |
|---|------|--------|-------|
| 2.1 | **Landing page redesign** (already requested): proper logged-out marketing page — hero with the one-line pitch, how-it-works (reuse wizard rule cards), live-ish stats (cycles run, decisions-vs-SPY), screenshots, CTA split *Join with invite / Try the demo / Join waitlist*. Fix the scroll-gradient break; design mobile-first (current card layout is already good on phones — keep that DNA). OG image, robots.txt, meta | 2–3 days | Robinhood's page is inspiration for confidence, not for copying |
| 2.2 | **Waitlist**: email capture table + endpoint + landing form; weekly digest of waitlist size | ½ day | Every interested stranger is currently lost; waitlist = cheapest traction metric |
| 2.3 | **Per-member referral codes**: `invite_codes` table (code, created_by, uses_remaining, revoked), admin mint/revoke, "your invite" card in the app; keeps `members.invite_code_used` lineage → an investor-legible growth graph | 1–2 days | Replaces the one shared env-var code |
| 2.4 | **Public demo instance** at demo.voteconcordia.com: second instance with DEMO_MODE=1, auto-filled invite, seeded history/bots, reset nightly; linked from landing | 1–2 days | The RH paste-back friction means **the demo IS the pitch** |
| 2.5 | Mobile QA pass on real phones (ballot sliders, cinematic overview, auth, wizard) + PWA manifest/icon so "add to home screen" works | 1–2 days | The 8am vote is a phone habit |
| 2.6 | RH-connect mitigation: annotated screenshot/GIF walkthrough inside the connect step; keep strangers in demo until they're sold | hours | Robinhood-imposed; mitigate, don't fight |
| 2.7 | Analytics: **cookieless/self-hosted** (Plausible or self-host Umami) + ~10 product events (wizard steps, signup funnel, connect fails, ballot cast) — **requires privacy-policy amendment** (current doc promises "no analytics trackers"; consents table already versions acceptances) | ½ day + 1h legal edit | |
| 2.8 | KPI endpoint + admin chart: signups curve, DAU/WAU, connect-funnel conversion, activation (connected+committed+opted-in), per-cycle turnout, executed notional, weekly total member equity (AUM-proxy from `voting_power_history`) | 1–2 days | **Highest-leverage pre-raise build**; schema already supports all of it |
| 2.9 | Admin UI v1 inside the product (role=owner route): overview, member drilldown, suspend/remove member, force-disconnect RH, ownership transfer, arm/halt, invite management, audit-log viewer, safe config knobs moved to kv | 3–5 days | Retires the forge-box dependency; member-removal is table stakes before strangers |

---

## 6. Phase 3 — Traction engine (Aug 11 – Aug 29)

*Goal: numbers that compound weekly. For a social product, engagement > user count.*

| # | Task | Effort | Notes |
|---|------|--------|-------|
| 3.1 | Engagement emails: vote-close reminder (pre-8:30 ET, opt-out), execution receipt, weekly performance digest | 1–2 days | A daily-vote product with no reminder loop decays; digests are retention evidence |
| 3.2 | Member wave 2: grow the club 4 → **15–30 real members** through friends-of-friends using referral codes; concierge-onboard every one; log every drop-off point | ongoing | Single club scales to ~25–30 comfortably; multi-club stays deferred (§13) |
| 3.3 | Build-in-public content loop: weekly "cycle recap" (decisions vs SPY, best/worst call, turnout) as tweet/short post; links to waitlist | 2h/week | The only free distribution that compounds |
| 3.4 | Weekly KPI snapshot ritual (from 2.8): members, activation %, 7-day ballot participation, committed capital, retention cohort — screenshotted every Friday into a running doc | 30min/week | This doc becomes the traction slide |
| 3.5 | **Targets to hit before pitching** (evidence, not vanity): 25+ members · 60%+ average daily ballot participation over 4 weeks · $25–50k aggregate committed · flat-or-up 8-week member retention · waitlist 100+ | — | Consumer pre-seeds are underwritten on engagement/retention, not revenue. **These numbers are now also the YC W27 application (§3)** — hitting them by mid-October is the single date that matters most in this plan |

---

## 7. Phase 4 — Corporate & legal rails (parallel, Aug)

*Nothing here blocks building; ALL of it blocks signing a SAFE.*

| # | Task | Effort/Cost | Notes |
|---|------|-------------|-------|
| 4.1 | **Delaware C-corp** (not LLC) via Clerky (~$427–819; financing docs generated in-system — better for a raise) or Stripe Atlas ($500) | days, mostly waiting | |
| 4.2 | Founder stock issuance w/ vesting + board consent; **83(b) within 30 days of the stock grant** (IRS Form 15620, e-filable since 2025; deadline is strict, no relief) | hours | Each founder's 30-day clock runs independently |
| 4.3 | **IP assignment (PIIA)** — the code is currently owned by Rohan personally; assign to the company. Decide Aryan's status (co-founder equity vs contractor) and paper it with a PIIA either way | hours | Missing IP assignment is the classic diligence killer |
| 4.4 | Ongoing: DE franchise tax ~$300–400/yr + registered agent ~$100–125/yr | — | |
| 4.5 | **Securities-counsel consult** (1–2 hours, fintech-native firm): bless the interim private-club posture, review the participation agreement, sanity-check the marketing language against the Marketing Rule | ~$500–1.5k | Do before the first partner meeting |
| 4.6 | Written **RIA-registration plan memo** (trigger points, state vs SEC path, cost/time estimate) — an internal doc that becomes the confident answer to the regulation question | ½ day w/ counsel input | |

---

## 8. Phase 5 — Broker derisking (Aug, interleaved)

*The #1 diligence question. RH's agentic API is single-user by its own docs; RH "does not control, supervise, monitor" agents — and could change terms any day.*

| # | Task | Effort | Notes |
|---|------|--------|-------|
| 5.1 | Formalize `BrokerClient` protocol from the existing seam (all I/O already flows through `vault.ensure_client`; DemoClient mirrors the full surface) + `broker` column on accounts | 2–3 days | Turns "we're an RH app" into "we're broker-agnostic rails" |
| 5.2 | **Alpaca spike**: one member account executing the daily basket via Alpaca's agent-first API (their Trading MCP + multi-account infra is explicitly built for orchestrators; $435M raised for exactly this in 2026) | 3–5 days | A 30-second "here it is on a second broker" demo defuses the question permanently |
| 5.3 | Evaluate SnapTrade (30+ broker unified read+trade aggregation) as the long-term "any broker" answer — research memo only for now | ½ day | Roadmap slide, not MVP |

---

## 9. Phase 6 — Fundraise materials & execution (Sep → Oct)

**The ask, benchmarked (Carta pre-seed data through Q1 2026):** raise **$750k–$1M on a post-money SAFE at a $6–10M cap**. Median cap for $250k–$1M raises is ~$10M; non-AI consumer fintech resets toward $6–10M; honest agentic-infra positioning may support the upper band — do not price like an AI-hype deal you can't defend.

| # | Task | Notes |
|---|------|-------|
| 6.1 | **Deck** (10–12 slides): Problem (investment clubs died of paperwork; investing is social everywhere except where the trade happens) · Insight (agent APIs make non-custodial collective execution possible for the first time) · Product (demo) · Traction (Phase-3 snapshot series) · Why-now (RH May-2026 agentic launch, Alpaca's agent pivot) · Market (Dub $45M raised, Autopilot ~$1B AUM & 90k+ paying subs on ~$8M raised — comps as market proof, differentiation: we're the only free, non-custodial, *collective* one) · Regulatory plan (one confident slide) · Roadmap (multi-club, multi-broker, monetization: club-tier subscriptions → RIA-gated premium) · Team · Ask & use of funds (registration counsel, first hire, growth) |
| 6.2 | **2-minute demo video** on the demo instance: signup → wizard → ballot → next-day execution receipt → performance chart. This carries every cold email |
| 6.3 | **One-pager PDF** + **data room** (KPI snapshots, architecture note, security posture summary — the audits are an asset, legal docs, cap table) |
| 6.4 | **Target list, sequenced.** Tier 1 (fintech pre-seed natives, warm-up first): Better Tomorrow Ventures ($500k–4M checks, explicitly idea/prototype-friendly, fresh $140M Fund III) · Nyca · Fin Capital · Anthemis. Tier 2 (proved the thesis by funding comps): Tusk (led Dub's seed) · Notable Capital & Neo (co-led Dub's A) · Craft/Nomad/Path (Autopilot). Tier 3: angels — fintech operators, RH/Public alumni, the Dara-Khosrowshahi-profile operator angels who did Dub's seed |
| 6.5 | **Sequencing around YC W27:** materials done by mid-Sep · angel conversations (Tier 3, small checks) can start late Sep — small angel SAFEs before YC are normal and don't complicate the standard deal · submit the YC app when the window opens (~Oct/Nov) · run Tier 1/2 institutional conversations Oct–Nov in parallel — do NOT gate them on YC; an acceptance (~Dec) only adds leverage, and if institutional interest converts first, that's a fine outcome too · if YC yes: batch Jan–Mar 2027, raise the remainder into Demo Day momentum |

---

## 10. KPI dictionary (define once, report weekly)

| KPI | Definition | Source |
|---|---|---|
| Members | rows in `members` excl. bots/deleted | `members` |
| Activation | % of members with RH connected AND stake committed AND opted in | `rh_accounts`+`member_settings` |
| DAU/WAU | distinct member logins per day/week | `login_events` (currently write-only — read it) |
| Ballot participation | voters ÷ eligible members, per cycle; 7-day rolling | `votes`/`vote_sessions` |
| Committed capital | Σ resolved member stakes (AUM-proxy) | `voting_power_history` weekly |
| Executed notional | Σ `executions.dollar_amount` per cycle + error rate | `executions` |
| Retention | week-N cohort still voting | `members`+`votes` |
| Referral K | invites sent → activated members per inviting member | `invite_codes` (new, 2.3) |
| Waitlist | emails captured; weekly delta | new table (2.2) |

---

## 11. Regulatory posture (updated by research — this section supersedes the earlier ratchet's optimism)

**Facts:** Dub (raised $45M+) and Autopilot (~$1B AUM) — the two funded products whose users' accounts trade on shared signals — are **both SEC-registered RIAs**. Autopilot is the structurally closest comp: non-custodial, connects to the user's own outside brokerage — and it *still* registered. The publisher's exclusion (Advisers Act §202(a)(11)(D)) very likely fails for us on the "general and impersonal" prong: a vote that automatically triggers a trade in an individual's account is personalized in the way that matters. "We're free" is weak armor: the compensation prong is construed broadly (indirect/planned monetization counts). eToro's US CopyTrader (Oct 2025) holds the "tool, not adviser" line — but from inside a registered broker-dealer, which we are not. There is no "investment club" safe harbor; the SEC decides on facts.

**Posture, staged:**
1. **Now → raise:** small, invite-only, free, no performance marketing, no personalized recommendations language anywhere; participation agreement + risk disclosure current (done); counsel consult blesses the interim (4.5). This is a private club among people who know each other — keep it literally true.
2. **At/after raise:** RIA registration (likely state-level first) is a **named use of funds**. The memo (4.6) makes this a plan with a date, not a hope.
3. **Never:** market AI capabilities beyond what's real (Delphia/Global Predictions were fined for exactly that), tout returns without Marketing-Rule-compliant context (Titan, $1.04M), or let bots back into the executable tally before counsel weighs in.

---

## 12. Risk register (and the answer you give when asked)

| Risk | Severity | The honest answer |
|---|---|---|
| **Robinhood ToS / single-user agentic scope** | High | Each member connects their own account individually — Concordia acts as each member's chosen agent, not a pooled orchestrator. And here's the same basket executing on Alpaca (5.2). Broker-agnostic by design |
| **Adviser-status challenge** | High | §11 posture; registration is a funded milestone, comps prove the path |
| **Solo-founder / bus factor** | Med | CI + runbooks + admin UI make ops transferable; Aryan papered (4.3); this raise funds hire #1 |
| **Consumer-fintech funding winter** | Med | We're capital-efficient to an absurd degree (€75/yr infra); engagement metrics, not burn, are the story |
| **SQLite / infra scale** | Low | Comfortable to ~1k members; migration path (Postgres/Litestream) documented; not an MVP problem |
| **Single club hardcoded** | Low | Deliberate wedge; multi-club is a scoped 2–4 week build (clubs table + club_id FK on ~8 tables) — say it with a straight face because it's true |

---

## 13. Consolidated build list by workstream (effort-ordered quick reference)

**Hours-scale:** bots-out flip · uptime+alerts · Sentry+error boundary · restore drill · daily_cap enforce-or-delete · /health redaction · CI · waitlist · member-suspend/remove · ownership-transfer endpoint · RH-connect walkthrough GIF · analytics events (+privacy amendment) · universe-refresh runbook entry
**Day-scale:** docs sweep · KPI endpoint+chart · referral codes · demo instance · engagement emails · mobile QA+PWA · landing page redesign
**Multi-day:** admin UI v1 (3–5d) · BrokerClient protocol (2–3d) · Alpaca spike (3–5d)
**Deferred (with prepared answers):** multi-club (2–4w) · SnapTrade any-broker (research only) · monetization build-out (post-RIA)

---

## 14. Sources (key)

- YC apply page (deadline Jul 27 2026 8PM PT; batch Oct–Dec) + YC blog ($500k standard deal structure)
- Carta *State of Pre-Seed* series Q1'25–Q1'26 (SAFE caps by raise size; consumer reset) — via indexed excerpts
- Dub: $17M seed (Feb 2024) + $30M Series A (Mar 2025, Notable/Neo); RIA + own broker-dealer (DASTA)
- Autopilot/Iris: ~$7–8M raised (Craft et al.); ~$1B AUM, 90k+ paying subs (Nov 2025); SEC RIA #331749; non-custodial, outside brokerages
- eToro US CopyTrader (Oct 2025): broker-dealer holding the "tool" line
- Robinhood "Open to Agents" (May 27 2026): agent.robinhood.com/mcp/trading, single-user Agentic account scope
- Alpaca: ~$435M raised 2026 for agent-first/prime infra; Trading MCP; multi-account APIs
- SEC: Titan (Marketing Rule, 2023) · Delphia/Global Predictions (AI-washing, Mar 2024) · Advisers Act §202(a)(11)(D) publisher's-exclusion commentary (Winstead 2025)
- Stripe Atlas / Clerky pricing; IRS Form 15620 (83(b) e-file since Jul 2025)
