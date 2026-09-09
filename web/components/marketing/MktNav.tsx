"use client";

// The page chrome: brand, section links, sign-in / join.
//
// position:FIXED (+ an in-flow spacer), never sticky. globals.css sets
// html,body{overflow-x:hidden}, which turns body into a scroll container and
// silently defeats every descendant position:sticky — the exact trap
// documented at the top of components/admin/OpsHeader.tsx. Same fix here.
//
// Because it is fixed, it cannot sit "under" the in-flow ticker by layout, so
// it is offset with a transform that shrinks as the ticker scrolls past:
// translateY(max(0, TICKER_H - scrollY)). At rest it lines up exactly with the
// bottom of the ticker; once the ticker is gone the nav is docked at y=0. The
// offset moves at the same rate as the page, so it reads as ordinary flow
// rather than as an animation — and it is a compositor-only transform.
//
// SPEC.md §6 bans fixed DECORATIVE tint layers, not chrome. The translucent
// wash below is a child of this header, so it travels with the bar and there
// is no seam for content to scroll through.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Logo } from "@/components/ui";
import { TICKER_H } from "./TopTicker";

// Mono microtype rather than sentence case: it is the texture the rest of the
// page is built from, and it keeps four links inside a 768px bar without the
// centre cluster colliding with the CTAs.
//
// Real routes since the five-page split (2026-09-09), not #anchors — next/link
// so moving between marketing pages stays client-side. Keep in step with
// MKT_PAGE_PATHS in seo.ts (AppShell + sitemap route on that list).
const LINKS: { href: string; label: string }[] = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/difference", label: "Difference" },
  { href: "/record", label: "Record" },
  { href: "/faq", label: "FAQ" },
];

const NAV_H = "h-[60px] sm:h-[68px]";

export function MktNav() {
  const pathname = usePathname();
  const [dock, setDock] = useState(TICKER_H);
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [reduced, setReduced] = useState(false);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  // Every transition on this bar is written inline (durations depend on state),
  // so the `prefers-reduced-motion` block in marketing.css — which only names
  // .mkt-reveal/.mkt-marquee/.mkt-caret/.mkt-btn/.mkt-card — cannot reach them.
  // The panel's max-height expansion and the burger's rotate are real motion,
  // so the setting is read here and collapses every duration to 0.
  // Starts false so SSR and the first client render agree; only durations
  // change when it flips, never layout.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  const ms = (n: number) => (reduced ? 0 : n);

  // One rAF-coalesced scroll listener drives both the dock offset and the
  // glass state, so a fast flick does no more than one write per frame.
  useEffect(() => {
    let raf = 0;
    const read = () => {
      raf = 0;
      const y = window.scrollY;
      setDock(Math.max(0, TICKER_H - y));
      setScrolled(y > 40);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(read); };
    read();                                   // correct after a mid-page reload
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  // Escape closes the panel. Deliberately no focus trap and no scroll lock:
  // this is a disclosure menu, not a modal, and trapping focus in a dropdown
  // is worse for keyboard users than leaving the page reachable behind it.
  //
  // Focus DOES have to come back to the toggle, though: the collapsed panel is
  // visibility:hidden, so a keyboard user who Escapes while focused on one of
  // its links has focus silently reset to <body> and resumes tabbing from the
  // top of the document.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Growing past the breakpoint reveals the centre links and hides the toggle;
  // without this, aria-expanded would keep claiming a panel nobody can see.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => { if (mq.matches) setOpen(false); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // The open panel needs the glass even at the top of the page, otherwise it
  // floats over the hero with nothing behind it.
  const solid = scrolled || open;

  return (
    <>
      <header
        className={`fixed inset-x-0 top-0 z-50 border-b ${
          solid ? "border-[var(--mkt-hair)]" : "border-transparent"
        }`}
        style={{
          transform: `translateY(${dock}px)`,
          backdropFilter: solid ? "saturate(150%) blur(18px)" : "saturate(100%) blur(0px)",
          WebkitBackdropFilter: solid ? "saturate(150%) blur(18px)" : "saturate(100%) blur(0px)",
          // transform is intentionally absent: it tracks scroll and must not lag.
          transition:
            `backdrop-filter ${ms(450)}ms var(--mkt-ease), -webkit-backdrop-filter ${ms(450)}ms var(--mkt-ease), border-color ${ms(450)}ms var(--mkt-ease)`,
        }}
      >
        {/* Opacity, not a background swap, so the fade is continuous and the
            colour stays the single canvas token. inset-0 covers the expanded
            header too, which is what gives the mobile panel its ground. */}
        <span
          aria-hidden
          className="absolute inset-0 bg-[var(--mkt-bg)]"
          style={{ opacity: solid ? 0.84 : 0, transition: `opacity ${ms(500)}ms var(--mkt-ease)` }}
        />

        <div className={`relative mx-auto flex w-full max-w-[1180px] items-center gap-2 px-5 sm:gap-3 sm:px-8 ${NAV_H}`}>
          {/* next/link like the page links beside it: the brand mark going
              through a full reload while the nav links swap client-side read
              as two different sites. */}
          <Link href="/" className="flex flex-none items-center gap-2.5" aria-label="Concordia — home">
            <Logo size={26} />
            <span className="font-display text-[16px] font-bold tracking-tight text-[var(--mkt-ink)]">
              concordia
            </span>
          </Link>

          {/* flex-1 rather than absolute centring: an absolutely centred group
              can overlap the CTAs at exactly 768px, where the bar is tightest. */}
          <nav
            aria-label="Site pages"
            className="hidden min-w-0 flex-1 items-center justify-center gap-0.5 md:flex"
          >
            {LINKS.map((l) => {
              const active = pathname === l.href;
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  aria-current={active ? "page" : undefined}
                  className={`mkt-mono whitespace-nowrap rounded-full px-2.5 py-2 transition-colors duration-300 hover:bg-[var(--mkt-panel)] hover:text-[var(--mkt-ink)] ${
                    active ? "bg-[var(--mkt-panel)] text-[var(--mkt-ink)]" : "text-[var(--mkt-muted)]"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex flex-none items-center gap-2">
            {/* Wrapped rather than putting `hidden` on the anchor: .mkt-btn sets
                display:inline-flex and, depending on stylesheet order, would
                win over Tailwind's .hidden. The wrapper cannot lose that race. */}
            <span className="hidden sm:block">
              <a href="/join" className="mkt-btn mkt-btn-ghost" style={{ padding: "0.5rem 1rem", fontSize: 13 }}>
                Sign in
              </a>
            </span>
            <a href="/join" className="mkt-btn mkt-btn-primary" style={{ padding: "0.5rem 1.05rem", fontSize: 13 }}>
              <span className="lg:hidden">Join</span>
              <span className="hidden lg:inline">Join with an invite</span>
            </a>

            <button
              ref={toggleRef}
              type="button"
              aria-expanded={open}
              aria-controls="mkt-menu"
              onClick={() => setOpen((v) => !v)}
              className="flex h-10 w-10 flex-none items-center justify-center rounded-full border border-[var(--mkt-hair)] text-[var(--mkt-ink)] transition-colors duration-300 hover:border-[var(--mkt-hair-warm)] md:hidden"
            >
              <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
              <span aria-hidden className="relative block h-[9px] w-[16px]">
                <span
                  className="absolute left-0 top-0 h-[1.5px] w-full rounded-full bg-current"
                  style={{ transform: open ? "translateY(3.75px) rotate(45deg)" : "none", transition: `transform ${ms(300)}ms var(--mkt-ease)` }}
                />
                <span
                  className="absolute bottom-0 left-0 h-[1.5px] w-full rounded-full bg-current"
                  style={{ transform: open ? "translateY(-3.75px) rotate(-45deg)" : "none", transition: `transform ${ms(300)}ms var(--mkt-ease)` }}
                />
              </span>
            </button>
          </div>
        </div>

        {/* Always mounted so aria-controls always resolves; collapsed with
            max-height AND visibility so the links leave the tab order when
            hidden (a height-0 panel alone stays focusable). visibility is
            delayed on close only, so it does not blink away mid-collapse. */}
        <div
          id="mkt-menu"
          className="relative overflow-hidden md:hidden"
          style={{
            maxHeight: open ? 380 : 0,
            visibility: open ? "visible" : "hidden",
            transition: open
              ? `max-height ${ms(340)}ms var(--mkt-ease), visibility 0s`
              : `max-height ${ms(280)}ms var(--mkt-ease), visibility 0s linear ${ms(280)}ms`,
          }}
        >
          <nav aria-label="Site pages" className="mx-auto w-full max-w-[1180px] px-5 pb-6 pt-1 sm:px-8">
            {LINKS.map((l) => {
              const active = pathname === l.href;
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`mkt-mono block border-t border-[var(--mkt-hair)] py-4 transition-colors duration-300 hover:text-[var(--mkt-ink)] ${
                    active ? "text-[var(--mkt-ink)]" : "text-[var(--mkt-muted)]"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
            <a
              href="/join"
              onClick={() => setOpen(false)}
              className="mkt-mono block border-t border-[var(--mkt-hair)] py-4 text-[var(--mkt-ink)] sm:hidden"
            >
              Sign in
            </a>
          </nav>
        </div>
      </header>

      {/* In-flow stand-in for the fixed bar. It matches the NAV only: the
          ticker above supplies its own height, and the panel must overlay the
          hero rather than push it down. */}
      <div aria-hidden className={NAV_H} />
    </>
  );
}

export default MktNav;
