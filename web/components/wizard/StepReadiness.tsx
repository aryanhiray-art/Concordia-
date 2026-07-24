"use client";

// Step 3 — "One quick Robinhood check": can the connected account actually
// place buys? Ready → success beat, auto-advance (only when this is the
// member's furthest step — revisits via Back get a manual Continue instead,
// so Back navigation is never fought by an auto-advance). The common blocker
// is Robinhood's investing-goals questionnaire, which gets its own
// walkthrough + a force re-check. Anything else: reason + Re-check + a quiet
// skip so nobody is ever trapped here.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, Dots } from "@/components/ui";

type Readiness = { connected: boolean; ready: boolean; reason: string; detail?: string };

export function StepReadiness({ onDone, auto }: { onDone: () => void; auto: boolean }) {
  const [res, setRes] = useState<Readiness | null>(null);
  const [checking, setChecking] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  const check = useCallback(async (force: boolean) => {
    setChecking(true); setErr(null);
    try {
      const r = await api.tradeReadiness(force);
      setRes(r);
      if (r.ready && auto) timer.current = setTimeout(onDone, 1500);
    } catch (e: any) {
      setErr(e?.message || "couldn't reach Robinhood to check");
    } finally { setChecking(false); }
  }, [auto, onDone]);

  // first look uses the cached readiness; Re-check forces a fresh one
  useEffect(() => { check(false); }, [check]);

  if (checking && !res && !err) {
    return (
      <div className="flex items-center gap-3 py-6 text-[13px] text-muted">
        <Dots /> Checking your account with Robinhood…
      </div>
    );
  }

  if (res?.ready) {
    return (
      <div className="enter">
        <div className="flex items-center gap-3.5 rounded-[14px] border border-green/30 bg-green/[0.07] p-4">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-full bg-green/15 text-[16px] font-bold text-green" aria-hidden>✓</span>
          <div className="font-display text-[15px] font-semibold text-green">Your account is clear to trade ✓</div>
        </div>
        {auto ? (
          <p className="mt-3 text-center text-[12.5px] text-subtle">Next: commit your stake…</p>
        ) : (
          <Button variant="go" className="mt-4 w-full" onClick={onDone}>Continue</Button>
        )}
      </div>
    );
  }

  if (res?.reason === "needs_questionnaire") {
    return (
      <div>
        <p className="text-[13.5px] leading-relaxed text-muted">
          Robinhood needs you to answer a few <b className="text-ink">investing-goals
          questions</b> before it lets the club place buys. One-time, about two minutes:
        </p>
        <ol className="ml-4 mt-3 list-decimal space-y-1.5 text-[13px] leading-relaxed text-muted">
          <li>Open the <b className="text-ink">Robinhood app</b>.</li>
          <li>Go to <b className="text-ink">Settings → Investing profile</b> (or start a $1 buy — it&rsquo;ll prompt you).</li>
          <li>Answer the questions, then come back here.</li>
        </ol>
        <Button variant="ok" className="mt-4" onClick={() => check(true)} busy={checking}>Re-check</Button>
      </div>
    );
  }

  // anything else (including a failed fetch): reason + Re-check + quiet skip
  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-red">
        ⚠ {err ?? `That check didn't pass yet — Robinhood says “${(res?.reason || "unknown").replace(/_/g, " ")}”.`}
      </p>
      {res?.detail && <p className="mt-1.5 text-[12.5px] leading-relaxed text-subtle">{res.detail}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="ok" onClick={() => check(true)} busy={checking}>Re-check</Button>
        <button onClick={onDone} className="text-[12px] text-subtle transition hover:text-muted">
          skip this check for now →
        </button>
      </div>
    </div>
  );
}
