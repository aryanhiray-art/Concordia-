"use client";

// The wizard's 5-step progress rail. Desktop: numbered circles + labels;
// mobile: compresses to dots (current = teal pill). Completed / previously
// reached steps are clickable so members can move freely among them.

const LABELS = ["Rules", "Connect", "Check", "Stake", "You're in"];

export function Stepper({
  step, unlocked, onGo,
}: {
  step: number; unlocked: number; onGo: (n: number) => void;
}) {
  return (
    <ol className="flex items-center gap-1.5 sm:gap-2" aria-label={`Setup step ${step} of 5`}>
      {LABELS.map((label, i) => {
        const n = i + 1;
        const done = n < step;
        const current = n === step;
        const reachable = n <= unlocked;
        return (
          <li key={label} className="flex items-center gap-1.5 sm:gap-2">
            {i > 0 && (
              <span
                className={`hidden h-px w-5 sm:relative sm:-top-2 sm:block ${done || current ? "bg-teal/40" : "bg-white/10"}`}
                aria-hidden
              />
            )}
            <button
              type="button"
              onClick={() => reachable && onGo(n)}
              disabled={!reachable}
              aria-current={current ? "step" : undefined}
              aria-label={`Step ${n}: ${label}${done ? " (done)" : ""}`}
              title={label}
              className="group flex flex-col items-center gap-1 disabled:cursor-default"
            >
              {/* ≥sm: the numbered circle */}
              <span
                className={`hidden h-7 w-7 place-items-center rounded-full text-[11.5px] font-bold transition sm:grid ${
                  current
                    ? "border border-teal/60 bg-teal/10 text-teal shadow-[0_0_16px_-4px_rgba(45,212,191,0.7)]"
                    : done
                    ? "bg-green/15 text-green"
                    : reachable
                    ? "border border-white/15 text-muted group-hover:border-teal/40 group-hover:text-teal"
                    : "border border-white/10 text-subtle"
                }`}
              >
                {done ? "✓" : n}
              </span>
              {/* mobile: the dot */}
              <span
                className={`block rounded-full transition-all sm:hidden ${
                  current
                    ? "h-2 w-5 bg-teal shadow-[0_0_10px_rgba(45,212,191,0.6)]"
                    : done
                    ? "h-2 w-2 bg-green/70"
                    : "h-2 w-2 bg-white/15"
                }`}
              />
              <span
                className={`hidden text-[10.5px] font-semibold sm:block ${
                  current ? "text-ink" : done ? "text-muted" : "text-subtle"
                }`}
              >
                {label}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
