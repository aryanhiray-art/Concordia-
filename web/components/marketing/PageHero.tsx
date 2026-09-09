"use client";

// The subpage opener — every page except home starts with one. A deliberate
// step DOWN from the home hero (mkt-h2, no figure): a visitor deep-linking to
// /record should land on the page's argument in one viewport, Robinhood
// product-page style, not on a second front door. Spacing pairs with the
// sections that follow (py-16 top / the section's own py-24 below).

import { Reveal, Eyebrow, Glow, Section, CTA } from "./primitives";

export function PageHero({
  eyebrow,
  title,
  lead,
  cta,
}: {
  eyebrow: string;
  title: React.ReactNode;
  lead: React.ReactNode;
  /** Optional CTA pair; most pages let the closing band do the asking. */
  cta?: { label: string; href: string; secondary?: { label: string; href: string } };
}) {
  return (
    <Section className="relative pb-4 pt-16 sm:pt-24">
      <Glow className="-top-24 right-[-12%]" tone="green" intensity={0.1} size={520} />
      <div className="relative max-w-[820px]">
        <Reveal>
          <Eyebrow>{eyebrow}</Eyebrow>
        </Reveal>
        <Reveal delay={70}>
          <h1 className="mkt-display mkt-h2 mt-5 text-[var(--mkt-ink)]">{title}</h1>
        </Reveal>
        <Reveal delay={140}>
          <p className="mkt-lead mt-6 max-w-[58ch]">{lead}</p>
        </Reveal>
        {cta && (
          <Reveal delay={200}>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <CTA href={cta.href}>{cta.label}</CTA>
              {cta.secondary && (
                <CTA href={cta.secondary.href} variant="ghost">
                  {cta.secondary.label}
                </CTA>
              )}
            </div>
          </Reveal>
        )}
      </div>
    </Section>
  );
}
