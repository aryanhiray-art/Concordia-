"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Account } from "@/lib/types";

type Ready = { connected: boolean; ready: boolean; reason: string } | null;

function Step({ n, done, title, children }: { n: number; done: boolean; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex gap-3.5">
      <div className={`mt-0.5 grid h-7 w-7 flex-none place-items-center rounded-full text-[12px] font-bold ${
        done ? "bg-green/15 text-green" : "border border-hair text-subtle"}`}>
        {done ? "✓" : n}
      </div>
      <div className="min-w-0 flex-1">
        <div className={`font-display text-[14.5px] font-semibold ${done ? "text-muted line-through" : "text-ink"}`}>{title}</div>
        {!done && children && <div className="mt-1.5 text-[12.5px] leading-relaxed text-muted">{children}</div>}
      </div>
    </div>
  );
}

export function Onboarding() {
  const { member } = useAuth();
  const [acc, setAcc] = useState<Account | null>(null);
  const [ready, setReady] = useState<Ready>(null);
  const [checking, setChecking] = useState(false);

  const connected = member?.robinhood?.status === "connected" && !!member?.robinhood?.account_number;
  const optedIn = Number(member?.settings?.opted_in) === 1;

  useEffect(() => {
    if (!connected) return;
    api.account().then(setAcc).catch(() => {});
    api.tradeReadiness().then(setReady).catch(() => {});
  }, [connected, member]);

  async function recheck() {
    setChecking(true);
    try { setReady(await api.tradeReadiness(true)); } finally { setChecking(false); }
  }

  const allocated = (acc?.allocation?.resolved_usd ?? 0) > 0;
  const tradeReady = ready?.ready === true;
  const allDone = connected && tradeReady && allocated && optedIn;
  if (allDone) return null;

  return (
    <div className="glass enter p-5 sm:p-6">
      <div className="flex items-center gap-2.5">
        <span className="dot bg-teal pulse" />
        <h2 className="font-display text-[16px] font-bold">Finish setting up</h2>
      </div>
      <p className="mt-1 text-[12.5px] text-subtle">A few one-time steps before your vote can trade.</p>

      <div className="mt-5 space-y-4">
        <Step n={1} done={connected} title="Connect your Robinhood (agentic) account">
          Link Robinhood on its own secure site — your credentials never touch Concordia.{" "}
          <Link href="/account" className="text-teal">Connect →</Link>
        </Step>

        <Step n={2} done={connected && tradeReady} title="Finish your Robinhood investing profile">
          {!connected ? (
            "Available after you connect."
          ) : ready?.reason === "needs_questionnaire" ? (
            <>
              Robinhood needs you to answer a few <b className="text-ink">investing-goals questions</b> before it
              lets the club place buys. Open the Robinhood app → try a $1 buy (or Settings → Investing profile) →
              answer them. Then{" "}
              <button onClick={recheck} disabled={checking} className="text-teal underline disabled:opacity-60">
                {checking ? "checking…" : "re-check"}</button>.
            </>
          ) : ready?.connected ? (
            <button onClick={recheck} disabled={checking} className="text-teal underline disabled:opacity-60">
              {checking ? "checking…" : "check status"}</button>
          ) : "Checking…"}
        </Step>

        <Step n={3} done={allocated} title="Set how much you commit">
          Choose a % or $ amount of your account for the club — that becomes your stake and voting power.{" "}
          <Link href="/account" className="text-teal">Set it →</Link>
        </Step>

        <Step n={4} done={optedIn} title="Turn on participation">
          Flip the switch in <Link href="/account" className="text-teal">Account</Link> to start voting and trading.
        </Step>
      </div>
    </div>
  );
}
