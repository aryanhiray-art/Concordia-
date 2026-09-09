// The hero. Entirely static — no fetch, no fallback state, nothing that can
// resolve into a spinner. Every claim below is SPEC.md §2/§3 verbatim or a
// direct paraphrase; the only number on the screen is the live-since date.

import { CTA, Glow, RotatingWord, Section } from "./primitives";

/* ── The convergence figure ────────────────────────────────────────────────
   The club's own idea drawn as geometry: many fine lines running inward from
   a dial and resolving into one small basket at the centre.

   Two constraints shaped it. First, SPEC §0 forbids publishing a headcount —
   so there are no discrete "member" nodes to count. 48 hairlines read as a
   texture, a direction of travel, not a roster. Second, the arcs echo the
   Logo's three concentric rings, so the art belongs to the brand rather than
   sitting next to it.

   Geometry is computed once at module scope from pure arithmetic (never
   Math.random), so server and client render byte-identical markup. */
const C = 210;                       // centre of the 420×420 viewBox
const SPOKES = Array.from({ length: 48 }, (_, i) => {
  const a = (i / 48) * Math.PI * 2 - Math.PI / 2;
  // Every fourth line runs longer and brighter — the rhythm that keeps a flat
  // fan from reading as a screentone.
  const major = i % 4 === 0;
  const at = (r: number) => [
    Number((C + Math.cos(a) * r).toFixed(2)),
    Number((C + Math.sin(a) * r).toFixed(2)),
  ] as const;
  const [x1, y1] = at(major ? 196 : 178);
  const [x2, y2] = at(100);
  return { x1, y1, x2, y2, o: major ? 0.68 : 0.22 };
});

function ConvergenceFigure() {
  return (
    <svg
      viewBox="0 0 420 420"
      className="h-auto w-full max-w-[290px] sm:max-w-[340px] lg:max-w-[380px]"
      aria-hidden
    >
      <defs>
        <radialGradient id="mkt-hero-core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--mkt-green)" stopOpacity="0.22" />
          <stop offset="42%" stopColor="var(--mkt-teal)" stopOpacity="0.08" />
          <stop offset="100%" stopColor="var(--mkt-teal)" stopOpacity="0" />
        </radialGradient>
        {/* `white` here is a luminance value for the mask channel, not a
            colour — a themed token would be meaningless in a mask. The stops
            let the fan hold its brightness where it converges and dissolve at
            the rim, so the figure has no hard outer edge. */}
        <radialGradient id="mkt-hero-fade" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="white" stopOpacity="1" />
          <stop offset="56%" stopColor="white" stopOpacity="0.92" />
          <stop offset="80%" stopColor="white" stopOpacity="0.3" />
          <stop offset="100%" stopColor="white" stopOpacity="0" />
        </radialGradient>
        <mask id="mkt-hero-mask">
          <rect width="420" height="420" fill="url(#mkt-hero-fade)" />
        </mask>
      </defs>

      <circle cx={C} cy={C} r={180} fill="url(#mkt-hero-core)" />

      {/* the fan: bright where it lands, dissolving where it starts */}
      <g mask="url(#mkt-hero-mask)" stroke="var(--mkt-teal)" strokeWidth={1}>
        {SPOKES.map((s, i) => (
          <line key={i} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} opacity={s.o} />
        ))}
      </g>

      <circle cx={C} cy={C} r={205} fill="none" stroke="var(--mkt-hair)" strokeWidth={1} />
      <circle cx={C} cy={C} r={196} fill="none" stroke="var(--mkt-hair-warm)" strokeWidth={1} />
      {/* the aperture the lines resolve through — dashed so it reads as a dial
          rather than a second solid ring competing with the rim. .mkt-dial
          turns it once per ~80s: the one ambient motion that makes the figure
          read as an instrument, not a poster (killed under reduced motion in
          marketing.css). CSS owns its transform — do not add a transform
          attribute here, the animation would clobber it. */}
      <circle
        className="mkt-dial"
        cx={C} cy={C} r={100} fill="none"
        stroke="var(--mkt-hair-warm)" strokeWidth={1}
        strokeDasharray="1.5 9" strokeLinecap="round"
      />

      {/* the cycle, part-way round. pathLength=360 lets the dash array be read
          as degrees — the same trick the Logo uses for its arcs. .mkt-arc is
          an opacity breath ONLY: this element's rotation lives in the
          transform ATTRIBUTE, which a CSS transform animation would override. */}
      <circle
        className="mkt-arc"
        cx={C} cy={C} r={196} fill="none"
        stroke="var(--mkt-green)" strokeWidth={2} strokeLinecap="round"
        pathLength={360} strokeDasharray="104 256"
        transform={`rotate(-96 ${C} ${C})`} opacity={0.9}
      />
      <rect
        x={187.5} y={11.7} width={6} height={6} rx={1}
        fill="var(--mkt-green)" transform="rotate(45 190.5 14.7)"
      />

      {/* The one basket everything resolves into. Three nested diamonds rather
          than a row of bars: bars read as a list (and, at this size, as a
          hamburger icon), and any countable set of holdings would invite a
          number the club does not publish. */}
      <rect
        x={148} y={148} width={124} height={124} rx={30}
        fill="var(--mkt-panel-2)" stroke="var(--mkt-hair-warm)" strokeWidth={1}
      />
      <g transform={`rotate(45 ${C} ${C})`}>
        <rect x={172} y={172} width={76} height={76} rx={8} fill="none" stroke="var(--mkt-hair-warm)" strokeWidth={1} />
        <rect x={186} y={186} width={48} height={48} rx={6} fill="none" stroke="var(--mkt-green)" strokeWidth={1} opacity={0.55} />
        <rect x={199} y={199} width={22} height={22} rx={4} fill="var(--mkt-green)" opacity={0.95} />
      </g>
    </svg>
  );
}

/* ── Trust row ─────────────────────────────────────────────────────────────
   The four facts a sceptical reader wants before the fold, all from SPEC §2.
   "Non-custodial" here is HOME'S ONE custody line (SPEC §4's once-per-page
   budget) — nothing else on this page may restate it. "One ballot per cycle"
   uses the fenced phrasing (never "one vote" — power is weighted). */
const TRUST = ["Free to members", "Invite-only", "One ballot per cycle", "Non-custodial"];

export function Hero() {
  return (
    <Section className="mkt-grain">
      {/* Section's inner box is `relative z-[2]`, so a -z-[1] child sits behind
          the copy but still inside the section — the glows travel with the
          hero instead of being pinned to the viewport (SPEC §6). No overflow
          on this layer, so the washes bleed past the 1180px column; the page
          guard that stops that from becoming a scrollbar is
          html,body{overflow-x:hidden} in globals.css — deliberately NOT an
          overflow rule on .mkt itself, which would establish a clip box that
          some engines apply to the fixed nav (see marketing.css). */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-[1]">
        {/* top-8, not a negative offset: .mkt-glow blurs 90px, and a wash that
            starts above the section paints over the ticker sitting above it
            (the section is position:relative, the ticker is not, so the glow
            wins the paint order). 32px of inset keeps the bloom inside. */}
        <Glow className="-left-44 top-8" size={760} />
        <Glow className="-right-24 top-[44%]" size={560} tone="green" intensity={0.1} />
      </div>

      {/* svh, not vh: on iOS vh is the TALL viewport, so a 88vh hero is taller
          than the screen the moment the URL bar is showing. */}
      {/* No "Live since" pill above the headline (owner call, 2026-09-09):
          the date already lives in the ticker and the proof strip's index
          note, and the headline lands harder with nothing over it. */}
      <div className="flex min-h-[88svh] flex-col justify-center pt-10 pb-14 sm:pt-14 lg:pt-16 lg:pb-20">
        {/* Three beats, one per line: the invitation, the wisdom engine, the
            output — all in the same clipped imperative register (the earlier
            "Being right earns more say. / One basket for the whole club."
            pair read sing-song next to line one; owner call, 2026-09-09).
            Line one is the hero's single moving element — a green rotating
            verb over the constant "together." Every word in the rotation must
            be independently TRUE of the product as "{Word} together." (they
            vote together, argue their way to a consensus, earn say together,
            decide together — SPEC §3). Line two is load-bearing for the
            rotation: "Earn more say." is what pins the "Earn together."
            state to SAY rather than returns — do not remove the earn→say
            pair from this line while "Earn" rotates ("get louder" was tried
            and rejected for exactly this: punchier, but it left "Earn"
            unanchored, §0). Line three claims where the basket executes for
            YOU — never "every account": participation is optional and the
            FAQ says so, so the universal form is provably false. The
            tagline's static form uses word one ("Vote"), so keep it first —
            seo.ts TAGLINE_LINES and the OG card render that form, and the
            two must not drift. Only line one carries green. */}
        <h1 className="mkt-display mkt-h1 text-[var(--mkt-ink)]">
          {/* Rotation order is deliberate: "Vote" first (it is the canonical
              tagline/OG state, the SSR paint, and what reduced-motion users
              see forever); "Argue" separates the near-synonyms Vote/Decide;
              "Earn" LAST and never first — standing alone it could read as an
              earnings promise; the lead's mechanism sentence is its anchor. */}
          <span className="block">
            <RotatingWord words={["Vote", "Argue", "Decide", "Earn"]} /> together.
          </span>
          <span className="block">Be right. Earn more say.</span>
          <span className="block">One basket, your own account.</span>
        </h1>

        {/* items-START, not centre or end: the figure is taller than the copy
            beside it, and centring it opened a dead 80px band under the
            headline. Starting both columns keeps the copy tight to the
            headline and lets the figure's mass fall to the lower right — the
            diagonal the whole composition is built on. */}
        <div className="mt-9 grid gap-12 lg:mt-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)] lg:items-start lg:gap-16">
          <div>
            {/* "sized to each member's own settings", never "executes the same
                for everyone" — participation and allocation are per-member
                (HowItWorks step 03; the FAQ's "What can Concordia actually
                do" and "What if I disagree" answers), and the tidier sentence
                is false. */}
            <p className="mkt-lead max-w-[52ch]">
              Each cycle, the club votes on which S&amp;P 500 names to hold.
              Voting power blends what you&rsquo;ve committed with how right
              you&rsquo;ve been — the club keeps learning who to listen to. One
              basket comes out, sized to your settings, in your own Robinhood
              account.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-3">
              <CTA href="/join">Join with an invite</CTA>
              <CTA href="https://demo.voteconcordia.com" variant="ghost" external>
                See the live demo
                {/* `external` means target=_blank; without this a screen-reader
                    or keyboard user gets no warning that the context changed.
                    sr-only is position:absolute, so it is not a flex item and
                    cannot open a gap inside .mkt-btn. */}
                <span className="sr-only"> (opens in a new tab)</span>
              </CTA>
            </div>

            {/* A 2×2 block on phones, one hairline-separated row from md up.
                Wrapping the row instead would put a leading hairline at the
                start of the second line, which reads as a stray mark. */}
            <ul className="mkt-mono mt-10 grid grid-cols-2 gap-x-6 gap-y-3 text-[var(--mkt-subtle)] md:flex md:flex-wrap md:items-center md:gap-x-4 md:gap-y-2">
              {TRUST.map((t, i) => (
                <li key={t} className="flex items-center gap-4">
                  {i > 0 && <span aria-hidden className="hidden h-3 w-px bg-[var(--mkt-hair)] md:block" />}
                  <span>{t}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* At lg+ the figure is the right-hand column. Below that the grid
              stacks and it closes the hero instead, centred and stepped down
              in size — dropping it entirely (the first cut) left every phone
              and tablet with a hero of nothing but text, which is the opposite
              of the mobile-first rule in SPEC §6. It sits AFTER the CTAs in
              source order, so it never pushes the buttons below the fold. */}
          <div className="flex justify-center lg:items-center lg:justify-end">
            <ConvergenceFigure />
          </div>
        </div>
      </div>
    </Section>
  );
}

export default Hero;
