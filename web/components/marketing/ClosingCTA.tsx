"use client";

// The last thing on the page: an oversized marquee of the club's one-line
// thesis, the two real doors (invite / sign in), and a waitlist form for
// everyone who has neither.
//
// The form is deliberately unglamorous. It is a real <form> with a real submit
// handler, a labelled required email input, a busy state and one status line
// under aria-live — because a marketing page that swallows an address in
// silence is worse than one with no form at all. Note what the success copy
// does NOT say: nothing was emailed to the visitor, so nothing here claims it
// was.

import { useState } from "react";

import { joinWaitlist } from "@/lib/publicApi";
import { CTA, Glow, Marquee, Reveal, Section } from "./primitives";

type Status = "idle" | "busy" | "done" | "error";

// Enough repeats that the doubled track is wider than any viewport — the
// Marquee keyframe translates by exactly -50%, which only reads as seamless if
// one copy already overflows the screen. At this type size four is plenty.
const BANNER = Array.from({ length: 4 }, () => "Nobody holds your money");

export function ClosingCTA() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (status === "busy" || status === "done") return;
    setStatus("busy");
    try {
      await joinWaitlist(email.trim());
      setStatus("done");
    } catch (err) {
      // The API answers 409 for an address it already knows. From the visitor's
      // side that is not a failure, and saying "you're already on the list"
      // out loud would leak whether an address is known — which is exactly what
      // the endpoint is built not to reveal. Both paths land on one message.
      const msg = err instanceof Error ? err.message : String(err);
      setStatus(/already|409/i.test(msg) ? "done" : "error");
    }
  }

  const busy = status === "busy";
  const locked = busy || status === "done";

  return (
    <Section id="join" width="full" className="relative overflow-hidden py-24 sm:py-32">
      <Glow className="left-1/2 top-[6%] -translate-x-1/2" tone="green" intensity={0.13} size={720} />

      {/* Oversized display marquee. It is decoration carrying a true sentence,
          so it is aria-hidden and repeated in the copy below in a form screen
          readers actually get. The child selector out-specifies .mkt-mono's
          11px, which is the one place on the page that type is not microtype. */}
      <div
        aria-hidden
        className="relative mb-14 select-none opacity-[0.09] [&_.mkt-mono]:text-[clamp(2.6rem,9vw,6.5rem)] [&_.mkt-mono]:tracking-[-0.02em] [&_.mkt-mono]:text-[var(--mkt-ink)] sm:mb-20"
      >
        <Marquee items={BANNER} durationS={34} separator="◆" />
      </div>

      <div className="relative mx-auto max-w-[720px] text-center">
        <Reveal>
          <h2 className="mkt-display mkt-h2 text-[var(--mkt-ink)]">
            Bring your own account.
          </h2>
        </Reveal>

        <Reveal delay={80}>
          <p className="mkt-lead mx-auto mt-6 max-w-[46ch]">
            Concordia is invite-only. With a code: connect your own Robinhood
            account, set how much of it the club may use, and vote. The club
            never takes custody of a cent. No code? Leave an email — invites go
            out in small rounds.
          </p>
        </Reveal>

        <Reveal delay={140}>
          <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
            <CTA href="/join">Join with an invite</CTA>
            {/* Not a second "Sign in": /join already IS the sign-in screen for
                anyone signed out, so pairing them here offered two differently
                named links to one destination — and for a screen-reader user,
                two identical hrefs read as a mistake. Without a code the
                honest next step is the demo, which is also the one thing that
                explains the product better than this page can. */}
            <CTA href="https://demo.voteconcordia.com" variant="ghost" external>
              See the live demo
            </CTA>
          </div>
        </Reveal>

        <Reveal delay={200}>
          <hr className="mkt-rule mx-auto mt-14 max-w-[420px]" />
        </Reveal>

        <Reveal delay={240}>
          <div className="mx-auto mt-10 max-w-[480px]">
            <p className="mkt-mono text-[var(--mkt-subtle)]">No invite yet</p>

            <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-2.5 sm:flex-row">
              <label htmlFor="mkt-waitlist-email" className="sr-only">
                Email address
              </label>
              <input
                id="mkt-waitlist-email"
                name="email"
                type="email"
                required
                autoComplete="email"
                inputMode="email"
                placeholder="you@example.com"
                value={email}
                disabled={locked}
                onChange={(e) => {
                  setEmail(e.target.value);
                  // Clear a stale failure the moment they start fixing it, so
                  // the live region never contradicts what is on screen.
                  if (status === "error") setStatus("idle");
                }}
                className="min-w-0 flex-1 rounded-full border border-[var(--mkt-hair)] bg-[var(--mkt-panel)] px-5 py-[0.82rem] text-[14px] text-[var(--mkt-ink)] outline-none transition-colors placeholder:text-[var(--mkt-subtle)] focus:border-[var(--mkt-hair-warm)] disabled:opacity-60"
              />
              <button type="submit" disabled={locked} className="mkt-btn mkt-btn-primary disabled:opacity-60">
                {busy ? "Sending…" : status === "done" ? "On the list" : "Request an invite"}
              </button>
            </form>

            {/* Always mounted: a live region added at the same moment as its
                text is frequently missed by screen readers. */}
            <p
              aria-live="polite"
              className="mt-3 min-h-[1.25rem] text-[13px] leading-relaxed text-[var(--mkt-muted)]"
            >
              {status === "done" &&
                "You're on the list. Invites go out in small rounds — nothing else to do for now."}
              {status === "error" && "That didn't go through. Try again in a moment."}
            </p>

            <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--mkt-subtle)]">
              An address only. Membership is never confirmed or denied here.
            </p>
          </div>
        </Reveal>
      </div>
    </Section>
  );
}
