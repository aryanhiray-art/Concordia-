// The masthead every app screen opens with.
//
// Layout is the newer one: a display title, a hairline, then a row of
// mono-labelled figures — the same shape the public site uses for its proof
// strip, so the app and the marketing page read as one product.
//
// The TYPE is Concordia's original, deliberately. An earlier pass set the title
// at weight 500 with -0.035em tracking, which is right for a landing page you
// read once and wrong for a screen you open every cycle: it reads as an
// editorial headline rather than as a place. Bold with tight tracking is what
// this app has always used, and it holds the top of the page better.
//
// It also replaces three hand-rolled <h1>s that had drifted to different sizes
// (28/34 on Vote, 34/40 on History and Account).

import type { ReactNode } from "react";

export type HeaderStat = {
  label: string;
  value: ReactNode;
  /** Caption under the figure — provenance, freshness, a qualifier. */
  sub?: ReactNode;
  /** Dim the figure when it is a placeholder rather than a real number. */
  muted?: boolean;
  tone?: "default" | "teal";
};

export function PageHeader({
  title, lead, stats, actions,
}: {
  title: string;
  lead?: ReactNode;
  stats?: HeaderStat[];
  /** Right-aligned controls belonging to the page as a whole. */
  actions?: ReactNode;
}) {
  return (
    <header className="enter">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <h1 className="font-display text-[34px] font-bold leading-none tracking-tight text-ink sm:text-[42px]">
            {title}
          </h1>
          {lead && <p className="mt-3 max-w-[54ch] text-[13.5px] leading-relaxed text-muted">{lead}</p>}
        </div>
        {actions && <div className="flex flex-none items-center gap-2.5">{actions}</div>}
      </div>

      {stats && stats.length > 0 && (
        <>
          {/* Fades at both ends — a hard full-width rule on a near-black page
              reads as a scar across the layout. */}
          <hr className="mt-7 h-px border-0 bg-[linear-gradient(90deg,transparent,var(--hair)_10%,var(--hair)_90%,transparent)]" />

          {/* Two columns on a phone, a divided row from sm up. items-start so a
              figure that carries a caption does not push its neighbours down. */}
          <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-6 sm:flex sm:flex-wrap sm:items-start sm:gap-y-0">
            {stats.map((s, i) => (
              <div
                key={s.label}
                className={`min-w-0 sm:pr-9 ${i > 0 ? "sm:ml-9 sm:border-l sm:border-hair sm:pl-9" : ""}`}
              >
                <dt className="label">{s.label}</dt>
                <dd
                  className={`mt-2.5 font-display text-[26px] font-bold leading-none tnum sm:text-[30px] ${
                    s.muted ? "text-subtle" : s.tone === "teal" ? "text-teal" : "text-ink"
                  }`}
                >
                  {s.value}
                </dd>
                {s.sub && <div className="mt-2 text-[11.5px] leading-snug text-subtle">{s.sub}</div>}
              </div>
            ))}
          </dl>
        </>
      )}
    </header>
  );
}
