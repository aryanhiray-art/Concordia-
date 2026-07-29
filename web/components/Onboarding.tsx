"use client";

// The slim resume banner for members who skipped the /welcome wizard with
// setup unfinished (the wizard itself replaced the old full checklist card).
// Shown on the Overview and the Account page; self-hides once every step is
// done. useSetupProgress is THE step accounting — the Account page reads it
// too, so "N of 5" can never drift between screens.

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** The five logical setup steps the resume bar counts: rules · connected ·
 *  readiness · stake · participation. (The wizard splits "connected" into an
 *  approve + paste screen, but it's one step of setup.) */
export const SETUP_STEPS = 5;

export function useSetupProgress(): {
  /** false while the readiness probe / account snapshot are still settling — render nothing. */
  loaded: boolean;
  done: number;
  complete: boolean;
} {
  // "allocated" comes from the shared account snapshot; only trade-readiness
  // (a different endpoint, with its own server-side TTL) is still fetched here.
  const { member, account, accountReady } = useAuth();
  const connected = member?.robinhood?.status === "connected" && !!member?.robinhood?.account_number;
  const optedIn = Number(member?.settings?.opted_in) === 1;
  const allocated = (account?.allocation?.resolved_usd ?? 0) > 0;
  const [state, setState] = useState<{ loaded: boolean; ready: boolean }>({ loaded: false, ready: false });

  useEffect(() => {
    if (!connected) { setState({ loaded: true, ready: false }); return; }
    let alive = true;
    api.tradeReadiness()
      .then((r) => { if (alive) setState({ loaded: true, ready: r.ready === true }); })
      .catch(() => { if (alive) setState({ loaded: true, ready: false }); });
    return () => { alive = false; };
  }, [connected, member]);

  // Step 1 (the rules) counts as done — anyone seeing the resume bar has been
  // through, or deliberately skipped, the wizard.
  const done = 1 + (connected ? 1 : 0) + (state.ready ? 1 : 0) + (allocated ? 1 : 0) + (optedIn ? 1 : 0);
  return { loaded: state.loaded && accountReady, done, complete: done >= SETUP_STEPS };
}

/** The bar itself, stateless — for callers that already ran useSetupProgress. */
export function SetupResumeBar({ done }: { done: number }) {
  return (
    <Link
      href="/welcome"
      className="glass shine enter flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3.5 transition hover:border-teal/30"
    >
      <span className="dot pulse flex-none bg-teal" />
      <span className="font-display text-[14px] font-semibold">Finish setting up</span>
      <span className="text-[12.5px] text-muted">{done} of {SETUP_STEPS} steps done</span>
      <span className="ml-auto whitespace-nowrap text-[13px] font-semibold text-teal">Resume →</span>
    </Link>
  );
}

export function Onboarding() {
  const { loaded, done, complete } = useSetupProgress();
  if (!loaded) return null;   // no flash while we find out
  if (complete) return null;  // fully set up — nothing to resume
  return <SetupResumeBar done={done} />;
}
