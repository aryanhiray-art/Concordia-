"use client";

// Step 1 — "How the club works": the rules, made friendly. Four compact rule
// cards (icon + title + two lines), then the CTA into the connect flow.
// Icons + copy share web/components/howicons.tsx with the logged-out landing,
// so a new member sees ONE visual language from first visit through onboarding.

import { Button } from "@/components/ui";
import { HOW_ICONS, HowChip } from "@/components/howicons";
import { useAuth } from "@/lib/auth";

const RULES: { icon: React.ReactNode; uid: string; title: string; body: React.ReactNode }[] = [
  {
    icon: HOW_ICONS.vote,
    uid: "rule1",
    title: "Vote on the basket",
    body: <>Every member votes for their stocks — the top picks become the club&apos;s basket.</>,
  },
  {
    icon: HOW_ICONS.stake,
    uid: "rule2",
    title: "You set the stake",
    body: <>You choose how much of your account plays, and only that slice ever trades.</>,
  },
  {
    icon: HOW_ICONS.yours,
    uid: "rule3",
    title: "Your account stays yours",
    body: <>Every trade runs in your own Robinhood — the club never touches your money.</>,
  },
  {
    icon: HOW_ICONS.control,
    uid: "rule4",
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
        Four things to know:
      </p>

      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {RULES.map((r) => (
          <div key={r.title} className="rounded-[14px] border border-white/10 bg-white/[0.03] p-3.5">
            <HowChip icon={r.icon} uid={r.uid} />
            <div className="mt-2 font-display text-[14px] font-semibold">{r.title}</div>
            <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{r.body}</p>
          </div>
        ))}
      </div>

      <Button variant="go" className="mt-4 w-full" onClick={onNext}>
        {connected ? "Continue" : "Got it — let's connect Robinhood"}
      </Button>
    </div>
  );
}
