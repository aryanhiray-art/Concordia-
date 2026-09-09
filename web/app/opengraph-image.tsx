import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import { TAGLINE, TAGLINE_LINES } from "@/components/marketing/seo";

// The card every link to voteconcordia.com unfurls into — iMessage, Slack,
// Discord, X, LinkedIn. It is the marketing page's first impression far more
// often than the page itself is.
//
// Rendered by satori + resvg, NOT a browser. That rules out most of what the
// rest of this surface is built from, and the constraints are load-bearing
// rather than stylistic:
//
//   * No CSS custom properties, so the palette below is duplicated from the
//     `.mkt` block in app/marketing.css as literals. This is the one place in
//     the marketing surface where a raw hex is unavoidable; keep the two in
//     sync by hand.
//   * No background-clip:text, no filter:blur, no @font-face, no class names.
//   * Every element with more than one child needs an explicit display:flex —
//     satori has no block layout, and an unset display silently drops all but
//     the first child.
//   * ONE font weight. satori does not synthesise bold, so hierarchy comes from
//     size, colour and tracking. That weight is Outfit SemiBold, committed at
//     app/fonts/Outfit-SemiBold.ttf (48KB) and read off disk here. The card is
//     the most-seen impression of the site, and typesetting it in a fallback
//     face while the page itself uses Outfit made it read as somebody else's
//     link. Fetching from Google at render time would put a network call on the
//     path; a committed binary has no such dependency.
//
// Deliberately carries NO performance figure. SPEC.md §0 requires the paper-
// index disclosure inside the same visual block as the number, and there is no
// honest way to fit it here — so the card leads with what the club IS.
export const runtime = "nodejs";
export const alt = `Concordia — ${TAGLINE}`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const BG = "#050809";
const INK = "#ecf2f0";
const MUTED = "#94a6a3";
const SUBTLE = "#6d807d";
const GREEN = "#34e3a8";

// The concordia mark — three concentric arcs, gaps facing right — as an <img>
// data URI rather than inline JSX SVG: satori hands data URIs straight to
// resvg, which is a complete SVG renderer, whereas its own inline-SVG path
// supports only a subset.
//
// app/icon.svg draws these with pathLength="360" so the dash arrays read as
// degrees. Here they are absolute lengths (the same fractions of 2πr) so the
// mark does not depend on the rasteriser implementing pathLength at all.
const MARK_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="34" fill="none" stroke="#A8E4CE" stroke-width="10"
    stroke-linecap="round" stroke-dasharray="169.12 44.51" transform="rotate(37.5 50 50)"/>
  <circle cx="50" cy="50" r="22" fill="none" stroke="#7FD1B6" stroke-width="9.5"
    stroke-linecap="round" stroke-dasharray="103.67 34.56" transform="rotate(45 50 50)"/>
  <circle cx="50" cy="50" r="10.5" fill="none" stroke="#4C8570" stroke-width="9"
    stroke-linecap="round" stroke-dasharray="45.81 20.16" transform="rotate(55 50 50)"/>
</svg>`;
const MARK = `data:image/svg+xml;utf8,${encodeURIComponent(MARK_SVG)}`;

/** The eyebrow lozenge from the page, which is a rotated square rather than a
 *  ◆ glyph — Noto Sans has no BLACK DIAMOND and would render tofu. */
function Diamond({ size: s = 8, opacity = 1 }: { size?: number; opacity?: number }) {
  return (
    <div
      style={{
        width: s,
        height: s,
        backgroundColor: GREEN,
        transform: "rotate(45deg)",
        opacity,
      }}
    />
  );
}

export default async function OpengraphImage() {
  // Read once per render; Next caches the generated PNG, so this is not a
  // per-request cost. process.cwd() is the app root in both dev and a
  // standalone production build.
  const outfit = await readFile(join(process.cwd(), "app/fonts/Outfit-SemiBold.ttf"));
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          position: "relative",
          backgroundColor: BG,
          color: INK,
          padding: "70px 78px",
          fontFamily: "Outfit",
        }}
      >
        {/* The one decorative element: an accent wash bled off the top-right
            corner, standing in for <Glow/>. A radial gradient, because satori
            has no blur filter to soften a solid shape with. */}
        <div
          style={{
            display: "flex",
            position: "absolute",
            top: -300,
            right: -240,
            width: 860,
            height: 860,
            backgroundImage:
              "radial-gradient(circle at center, rgba(52,227,168,0.20) 0%, rgba(52,227,168,0.05) 45%, rgba(52,227,168,0) 70%)",
          }}
        />

        {/* ── Identity row ── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            {/* Decorative: the wordmark beside it carries the name, and `alt`
                above describes the card as a whole. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={MARK} alt="" width={56} height={56} />
            <div style={{ fontSize: 36, letterSpacing: "-0.03em" }}>concordia</div>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 18,
              letterSpacing: "0.18em",
              color: SUBTLE,
            }}
          >
            <Diamond />
            <div>INVITE-ONLY INVESTING CLUB</div>
          </div>
        </div>

        {/* ── Headline ── */}
        <div style={{ display: "flex", gap: 36 }}>
          {/* Pull-quote rule. Stretches to the text block's height on its own:
              yoga's default align-items is stretch. */}
          <div
            style={{
              display: "flex",
              width: 3,
              backgroundImage:
                "linear-gradient(180deg, rgba(52,227,168,0.9) 0%, rgba(52,227,168,0.06) 100%)",
            }}
          />
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              // Satori doesn't shrink-to-fit: the longest tagline line
              // ("More right, more power.", 23 chars) fits ~1005px of card at
              // 74. Re-measure here if TAGLINE_LINES ever grows a longer line.
              fontSize: 74,
              lineHeight: 1.08,
              letterSpacing: "-0.035em",
            }}
          >
            {TAGLINE_LINES.map((line, i) => (
              // Line one takes the accent: on the page it is the green
              // rotating verb ("Vote together."), and the share card mirrors
              // the page's one green element.
              <div key={line} style={{ color: i === 0 ? GREEN : INK }}>
                {line}
              </div>
            ))}
          </div>
        </div>

        {/* ── Facts rail (SPEC.md §2 only) ── */}
        <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
          <div
            style={{
              display: "flex",
              width: "100%",
              height: 1,
              backgroundColor: "rgba(236,242,240,0.10)",
            }}
          />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 22,
              marginTop: 28,
              fontSize: 18,
              letterSpacing: "0.16em",
              color: MUTED,
            }}
          >
            <div>LIVE SINCE JUNE 2026</div>
            <Diamond size={7} opacity={0.65} />
            <div>~20 S&amp;P 500 NAMES A CYCLE</div>
            <Diamond size={7} opacity={0.65} />
            <div>FREE TO MEMBERS</div>
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [{ name: "Outfit", data: outfit, weight: 600, style: "normal" }],
    },
  );
}
