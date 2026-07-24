"use client";

// Step 1 — "How the club works": the rules, made friendly. Four compact rule
// cards (icon + title + two lines), then the CTA into the connect flow.

import { Button } from "@/components/ui";
import { useAuth } from "@/lib/auth";

function Icon({ children }: { children: React.ReactNode }) {
  return (
    <span className="ci" aria-hidden>
      <svg viewBox="0 0 24 24">{children}</svg>
    </span>
  );
}

const RULES: { icon: React.ReactNode; title: string; body: React.ReactNode }[] = [
  {
    icon: (
      <Icon>
        <rect x="4" y="4" width="16" height="16" rx="3" />
        <path d="M8.5 12.5l2.4 2.4 4.6-5" />
      </Icon>
    ),
    title: "One ballot a day",
    body: <>Everyone votes once a day — the top names become the club basket.</>,
  },
  {
    icon: (
      <Icon>
        <path d="M12 3l7 3v5c0 4.6-3 7.6-7 9-4-1.4-7-4.4-7-9V6z" />
        <path d="M12 9v5M9.8 11.2h4.4" />
      </Icon>
    ),
    title: "Your account, your money",
    body: <>Trades happen in <b className="text-ink">your</b> Robinhood — the club never holds your funds.</>,
  },
  {
    icon: (
      <Icon>
        <rect x="4.5" y="8" width="3.8" height="11" rx="1.2" />
        <rect x="10.1" y="8" width="3.8" height="11" rx="1.2" />
        <rect x="15.7" y="8" width="3.8" height="11" rx="1.2" />
        <path d="M4.5 4.5h15.2" />
      </Icon>
    ),
    title: "Equal-weight, automatic",
    body: <>The decided basket is bought for every participant, sized to what they committed.</>,
  },
  {
    icon: (
      <Icon>
        <rect x="3" y="8" width="18" height="8" rx="4" />
        <circle cx="16" cy="12" r="2.4" />
      </Icon>
    ),
    title: "You're always in control",
    body: (
      <>
        Pause with one switch; disconnect or delete anytime.{" "}
        <a
          href="/terms#risks" target="_blank" rel="noopener noreferrer"
          className="text-teal underline decoration-teal/40 underline-offset-2 hover:decoration-teal"
        >
          Risks are real
        </a>{" "}
        — you can lose what you commit.
      </>
    ),
  },
];

export function StepRules({ onNext }: { onNext: () => void }) {
  const { member } = useAuth();
  const connected = member?.robinhood?.status === "connected" && !!member?.robinhood?.account_number;

  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-muted">
        Concordia is a trading club that decides together. Four things to know:
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {RULES.map((r) => (
          <div key={r.title} className="rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
            {r.icon}
            <div className="mt-2.5 font-display text-[14px] font-semibold">{r.title}</div>
            <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{r.body}</p>
          </div>
        ))}
      </div>

      <Button variant="go" className="mt-5 w-full" onClick={onNext}>
        {connected ? "Continue" : "Got it — let's connect Robinhood"}
      </Button>
    </div>
  );
}
