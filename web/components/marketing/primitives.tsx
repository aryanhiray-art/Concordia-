"use client";

// Shared building blocks for the public marketing site. Every section composes
// these, so spacing, reveal timing and microtype stay identical page-wide —
// the thing that most often drifts when a landing page grows section by
// section. Visual tokens live in app/marketing.css under `.mkt`.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

// useLayoutEffect warns when React renders on the server. The marketing page is
// client-rendered inside AppShell, but the module is still evaluated during SSR,
// so alias to useEffect there.
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/* ── Scroll reveal ─────────────────────────────────────────────────────────
   One IntersectionObserver per element, disconnected after it fires: this is
   a page of ~60 revealed nodes and re-animating on scroll-back is noise.
   `delay` staggers siblings without needing a wrapper component. */
export function Reveal({
  children, delay = 0, className = "", as: Tag = "div",
}: {
  children: React.ReactNode; delay?: number; className?: string;
  as?: "div" | "section" | "li" | "article" | "header";
}) {
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Respect the OS setting here too, not just in CSS: with reduced motion we
    // never add .is-in, and the media query keeps the element visible.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting) return;
        el.style.transitionDelay = `${delay}ms`;
        el.classList.add("is-in");
        io.disconnect();
      },
      // Fire slightly BEFORE the element reaches the viewport edge, so the
      // motion has finished by the time it is properly in view.
      { rootMargin: "0px 0px -12% 0px", threshold: 0.08 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [delay]);
  return (
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <Tag ref={ref as any} className={`mkt-reveal ${className}`}>{children}</Tag>
  );
}

/* ── Section scaffold ─────────────────────────────────────────────────────── */
export function Section({
  id, children, className = "", width = "wide",
}: {
  id?: string; children: React.ReactNode; className?: string;
  width?: "wide" | "narrow" | "full";
}) {
  const w = width === "narrow" ? "max-w-[860px]" : width === "full" ? "max-w-none" : "max-w-[1180px]";
  return (
    <section id={id} className={`relative ${className}`}>
      <div className={`relative z-[2] mx-auto w-full ${w} px-5 sm:px-8`}>{children}</div>
    </section>
  );
}

/** The mono eyebrow that opens most sections: "◆ 02 — HOW IT WORKS". */
export function Eyebrow({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`mkt-mono flex items-center gap-2.5 text-[var(--mkt-subtle)] ${className}`}>
      <span aria-hidden className="h-[5px] w-[5px] rotate-45 bg-[var(--mkt-green)]" />
      {children}
    </div>
  );
}

/** Section header: eyebrow + display heading + optional lead paragraph. */
export function SectionHead({
  eyebrow, title, lead, align = "left", className = "",
}: {
  eyebrow?: React.ReactNode; title: React.ReactNode; lead?: React.ReactNode;
  align?: "left" | "center"; className?: string;
}) {
  const c = align === "center";
  return (
    <div className={`${c ? "mx-auto max-w-[720px] text-center" : "max-w-[760px]"} ${className}`}>
      {eyebrow && (
        <Reveal>
          <Eyebrow className={c ? "justify-center" : ""}>{eyebrow}</Eyebrow>
        </Reveal>
      )}
      <Reveal delay={60}>
        <h2 className="mkt-display mkt-h2 mt-5 text-[var(--mkt-ink)]">{title}</h2>
      </Reveal>
      {lead && <Reveal delay={120}><p className="mkt-lead mt-5">{lead}</p></Reveal>}
    </div>
  );
}

/* ── Glow ──────────────────────────────────────────────────────────────────
   Decorative wash, ABSOLUTE inside its section (never fixed — see the header
   comment in marketing.css for why that rule exists).

   Takes a TOKEN plus an intensity rather than a colour string. Every call site
   used to hand-write `rgba(52,227,168,0.09)` — the channel values of
   --mkt-green restated from memory — so retuning the palette would have
   silently left nine glows on the old hue. color-mix() keeps the hue owned by
   the token; on an engine without it the background is simply invalid and the
   wash does not paint, which for a decorative blur is the right failure. */
export function Glow({
  className = "", tone = "teal", intensity = 0.16, size = 620,
}: {
  className?: string;
  tone?: "teal" | "green" | "signal";
  /** 0–1, the token's opacity in the wash. */
  intensity?: number;
  size?: number;
}) {
  const token = `var(--mkt-${tone})`;
  return (
    <span
      aria-hidden
      className={`mkt-glow ${className}`}
      style={{
        width: size,
        height: size,
        background: `color-mix(in srgb, ${token} ${(intensity * 100).toFixed(1)}%, transparent)`,
      }}
    />
  );
}

/* ── Buttons ───────────────────────────────────────────────────────────────
   Anchors, not <button>s: every CTA on this page is a navigation. */
export function CTA({
  href, children, variant = "primary", className = "", external = false,
}: {
  href: string; children: React.ReactNode; variant?: "primary" | "ghost";
  className?: string; external?: boolean;
}) {
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={`mkt-btn mkt-btn-${variant} ${className}`}
    >
      {children}
    </a>
  );
}

/** Small status pill — "◆ LIVE SINCE JUNE 2026". */
export function Tag({ children, dot = false }: { children: React.ReactNode; dot?: boolean }) {
  return (
    <span className="mkt-pill">
      {dot && (
        <span aria-hidden className="relative flex h-[6px] w-[6px]">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--mkt-green)] opacity-60" />
          <span className="relative inline-flex h-[6px] w-[6px] rounded-full bg-[var(--mkt-green)]" />
        </span>
      )}
      {children}
    </span>
  );
}

/* ── Marquee ───────────────────────────────────────────────────────────────
   Renders the item list TWICE; the keyframe translates by exactly -50%, so the
   second copy lands where the first began and the loop is seamless. */
export function Marquee({
  items, durationS = 42, className = "", separator = "◆",
}: { items: ReactNode[]; durationS?: number; className?: string; separator?: string }) {
  const track = (key: string) => (
    <div className="mkt-marquee-track" key={key} aria-hidden={key === "b"}>
      {items.map((t, i) => (
        <span key={i} className="mkt-mono flex flex-none items-center whitespace-nowrap text-[var(--mkt-subtle)]">
          <span className="px-4">{t}</span>
          <span className="text-[var(--mkt-green)] opacity-70">{separator}</span>
        </span>
      ))}
    </div>
  );
  return (
    <div className={`mkt-marquee-mask mkt-marquee-pause ${className}`}>
      <div className="mkt-marquee" style={{ ["--mkt-dur" as string]: `${durationS}s` }}>
        {track("a")}
        {track("b")}
      </div>
    </div>
  );
}

/* ── Count-up ──────────────────────────────────────────────────────────────
   Animates once when scrolled into view. Starts AT the final value in state so
   a no-JS / reduced-motion / never-observed render still shows the real
   number — a stat that reads "0" because an observer never fired is worse than
   no animation at all. */
export function CountUp({
  to, decimals = 0, prefix = "", suffix = "", durationMs = 1100, className = "",
}: {
  to: number; decimals?: number; prefix?: string; suffix?: string;
  durationMs?: number; className?: string;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [v, setV] = useState(to);

  // Zeroing happens in a LAYOUT effect, not the observer callback. The proof
  // strip sits directly under the hero, so it is usually already intersecting
  // on load — arming inside the callback meant the real number painted first
  // and then visibly snapped back to 0 before counting up. Running before
  // paint makes the count start at 0 with no flash, while SSR and the
  // no-JS/reduced-motion paths still render the true value.
  useIsomorphicLayoutEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setV(0);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setV(to); return; }
    let raf = 0;
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      const t0 = performance.now();
      const tick = (now: number) => {
        const p = Math.min(1, (now - t0) / durationMs);
        const eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic
        // Land on the EXACT target: easing asymptotes, and a stat reading
        // "+6.84%" because the last frame fell a hair short is a wrong number.
        setV(p >= 1 ? to : to * eased);
        if (p < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }, { threshold: 0.5 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [to, durationMs]);

  return (
    <span ref={ref} className={`tnum ${className}`}>
      {prefix}{v.toFixed(decimals)}{suffix}
    </span>
  );
}

/* ── Rotating word ─────────────────────────────────────────────────────────
   The hero's one moving headline element. Reserves the width of the LONGEST
   word so the line above never reflows as it cycles. */
export function RotatingWord({ words, intervalMs = 2600 }: { words: string[]; intervalMs?: number }) {
  const [i, setI] = useState(0);
  const [on, setOn] = useState(true);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => {
      setOn(false);
      setTimeout(() => { setI((n) => (n + 1) % words.length); setOn(true); }, 280);
    }, intervalMs);
    return () => clearInterval(id);
  }, [words.length, intervalMs]);

  return (
    <span className="relative inline-grid align-bottom">
      {/* invisible sizer: the longest word, holding the column width open */}
      <span aria-hidden className="invisible col-start-1 row-start-1 whitespace-nowrap">
        {words.reduce((a, b) => (b.length > a.length ? b : a), "")}
      </span>
      <span
        className="col-start-1 row-start-1 whitespace-nowrap text-[var(--mkt-green)] transition-all duration-300"
        style={{ opacity: on ? 1 : 0, transform: on ? "none" : "translateY(-0.18em)" }}
      >
        {words[i]}
      </span>
    </span>
  );
}
