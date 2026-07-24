"use client";

// The slim resume banner for members who skipped the /welcome wizard with
// setup unfinished (the wizard itself replaced the old full checklist card).
// Shown on the Overview; self-hides once every step is done.

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function Onboarding() {
  const { member } = useAuth();
  const connected = member?.robinhood?.status === "connected" && !!member?.robinhood?.account_number;
  const optedIn = Number(member?.settings?.opted_in) === 1;
  const [state, setState] = useState<{ loaded: boolean; ready: boolean; allocated: boolean }>(
    { loaded: false, ready: false, allocated: false },
  );

  useEffect(() => {
    if (!connected) { setState({ loaded: true, ready: false, allocated: false }); return; }
    let alive = true;
    Promise.allSettled([api.tradeReadiness(), api.account()]).then(([r, a]) => {
      if (!alive) return;
      setState({
        loaded: true,
        ready: r.status === "fulfilled" && r.value.ready === true,
        allocated: a.status === "fulfilled" && (a.value.allocation?.resolved_usd ?? 0) > 0,
      });
    });
    return () => { alive = false; };
  }, [connected, member]);

  if (!state.loaded) return null; // no flash while we find out
  // Step 1 (the rules) counts as done — anyone seeing this banner has been
  // through, or deliberately skipped, the wizard.
  const done = 1 + (connected ? 1 : 0) + (state.ready ? 1 : 0) + (state.allocated ? 1 : 0) + (optedIn ? 1 : 0);
  if (done >= 5) return null; // fully set up — nothing to resume

  return (
    <Link
      href="/welcome"
      className="glass shine enter flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3.5 transition hover:border-teal/30"
    >
      <span className="dot pulse flex-none bg-teal" />
      <span className="font-display text-[14px] font-semibold">Finish setting up</span>
      <span className="text-[12.5px] text-muted">{done} of 5 steps done</span>
      <span className="ml-auto whitespace-nowrap text-[13px] font-semibold text-teal">Resume →</span>
    </Link>
  );
}
