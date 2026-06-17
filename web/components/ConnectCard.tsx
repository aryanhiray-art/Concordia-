"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Member } from "@/lib/types";
import { Button, Card, Pill, Toggle } from "./ui";

export function ConnectCard({ member, onChange }: { member: Member; onChange: () => void }) {
  const rh = member.robinhood;
  const s = member.settings;
  const connected = rh && rh.status === "connected" && rh.account_number;
  const agentic = rh && Number(rh.agentic_allowed) === 1;

  const [optedIn, setOptedIn] = useState(Number(s?.opted_in) === 1);
  const [busy, setBusy] = useState(false);

  async function connect() {
    setBusy(true);
    try { const { authorize_url } = await api.rhStart(); window.location.href = authorize_url; }
    finally { setBusy(false); }
  }

  // The single on/off switch — persists immediately, stays where you leave it.
  async function toggle(on: boolean) {
    setOptedIn(on);
    try { await api.participate(on); onChange(); }
    catch { setOptedIn(!on); } // revert on failure
  }

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="label">Your account</h2>
        {connected
          ? (agentic ? <Pill tone="ok">● connected · agentic</Pill> : <Pill tone="warn">connected · not agentic</Pill>)
          : <Pill tone="danger">not connected</Pill>}
      </div>

      {!connected ? (
        <div className="mt-4">
          <p className="text-[13.5px] leading-relaxed text-muted">
            Connect Robinhood to take part. You log in on Robinhood&apos;s own site and approve
            agentic trading — your credentials never touch Concord.
          </p>
          <Button variant="go" className="mt-4" onClick={connect} busy={busy}>
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
              strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
            Connect Robinhood
          </Button>
        </div>
      ) : (
        <>
          <div className="mt-3 text-[13px] text-muted">
            Account <span className="font-display text-ink">···{String(rh!.account_number).slice(-4)}</span>
            {!agentic && <span className="ml-2 text-amber">— enable Robinhood&apos;s “Agentic” account to trade.</span>}
          </div>

          <div className="mt-5 flex items-center justify-between gap-4 border-t border-white/10 pt-5">
            <div>
              <div className="font-display text-[15px] font-semibold">
                {optedIn ? <span className="text-teal">Participating today</span> : "Sitting out today"}
              </div>
              <div className="mt-0.5 text-[12px] text-subtle">
                {optedIn
                  ? "Your committed amount is deployed across the basket each day."
                  : "Voting is off. Flip this on to vote and trade today."}
              </div>
            </div>
            <Toggle on={optedIn} onChange={toggle} />
          </div>
        </>
      )}
    </Card>
  );
}
