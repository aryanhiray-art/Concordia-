"use client";

// Step 6 — "You're in": the participation switch (api.participate) with one
// line on what it means, a recap of the member's setup, and the door into the
// club. Deliberately confetti-free.

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Toggle } from "@/components/ui";
import { money } from "./money";

export function StepFinish({ onEnter }: { onEnter: () => void }) {
  // The recap reads the shared snapshot — StepStake's Commit pushed its result
  // straight into the context, so this is already the number just saved.
  const { member, account: acc, refreshMember } = useAuth();
  const [on, setOn] = useState(Number(member?.settings?.opted_in) === 1);
  const [err, setErr] = useState<string | null>(null);

  // persists immediately, stays where you leave it (revert on failure)
  async function toggle(v: boolean) {
    setOn(v); setErr(null);
    try { await api.participate(v); await refreshMember(); }
    catch (e: any) { setOn(!v); setErr(e?.message || "couldn't update participation"); }
  }

  const rhNum = member?.robinhood?.account_number;
  const last4 = rhNum ? `····${String(rhNum).slice(-4)}` : "—";

  return (
    <div>
      <div className="flex items-center justify-between gap-4 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
        <div className="min-w-0">
          <div className="font-display text-[15px] font-semibold">
            {on ? <span className="text-teal">Participating</span> : "Participation is off"}
          </div>
          <div className="mt-0.5 text-[12px] leading-relaxed text-subtle">
            {on
              ? "You vote daily and the decided basket trades your slice."
              : "Flip on to vote daily and trade the decided basket."}
          </div>
        </div>
        <Toggle on={on} onChange={toggle} />
      </div>
      {err && <div className="mt-2 text-[12px] text-red">⚠ {err}</div>}

      <div className="mt-4 rounded-[14px] border border-teal/20 bg-teal/[0.04] p-4">
        <div className="label mb-3">Your setup</div>
        <dl className="space-y-2.5 text-[13px]">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-muted">Robinhood account</dt>
            <dd className="font-display font-semibold text-ink">{last4}</dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-muted">Committed</dt>
            <dd className="font-display font-semibold text-teal tnum">
              {acc ? money(acc.allocation.resolved_usd, 2) : "…"}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-muted">Voting power</dt>
            <dd className="font-display font-semibold text-ink tnum">
              {acc ? money(acc.power.power) : "…"}
            </dd>
          </div>
        </dl>
      </div>

      <Button variant="go" className="mt-4 w-full" onClick={onEnter}>Enter the club →</Button>
    </div>
  );
}
