"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Member } from "@/lib/types";
import { Button, Card, Pill } from "./ui";

export function ConnectCard({ member, onChange }: { member: Member; onChange: () => void }) {
  const rh = member.robinhood;
  const s = member.settings;
  const connected = rh && rh.status === "connected" && rh.account_number;
  const agentic = rh && Number(rh.agentic_allowed) === 1;

  const [pct, setPct] = useState(s?.buying_power_pct ?? 10);
  const [cap, setCap] = useState(s?.daily_cap_usd ?? 500);
  const [optedIn, setOptedIn] = useState(Number(s?.opted_in) === 1);
  const [busy, setBusy] = useState(false);

  async function connect() {
    setBusy(true);
    try {
      const { authorize_url } = await api.rhStart();
      window.location.href = authorize_url;
    } finally { setBusy(false); }
  }

  async function saveSettings() {
    setBusy(true);
    try { await api.updateSettings(optedIn, Number(pct), Number(cap)); onChange(); }
    finally { setBusy(false); }
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

          <div className="mt-5 grid gap-4 border-t border-white/10 pt-5 sm:grid-cols-2">
            <button onClick={() => setOptedIn((v) => !v)}
              className="flex items-center justify-between rounded-[12px] glass-2 border border-white/10 px-3.5 py-2.5 text-left">
              <span className="text-[13.5px]">Participate today</span>
              <span className={`relative h-5 w-9 rounded-full transition ${optedIn ? "bg-teal-deep" : "bg-white/10"}`}>
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${optedIn ? "left-[18px]" : "left-0.5"}`} />
              </span>
            </button>
            <div className="hidden sm:block" />
            <label className="flex items-center justify-between text-[13.5px]">
              <span className="text-muted">Buying-power %</span>
              <input className="input !w-24 !py-1.5 text-right tnum" type="number" min={0} max={100}
                value={pct} onChange={(e) => setPct(e.target.valueAsNumber)} />
            </label>
            <label className="flex items-center justify-between text-[13.5px]">
              <span className="text-muted">Daily cap (USD)</span>
              <input className="input !w-24 !py-1.5 text-right tnum" type="number" min={0}
                value={cap} onChange={(e) => setCap(e.target.valueAsNumber)} />
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="ok" onClick={saveSettings} busy={busy}>Save</Button>
            <span className="text-[11.5px] text-subtle">
              Each pick gets ≈ your BP% ÷ number of picks, capped at your daily limit.
            </span>
          </div>
        </>
      )}
    </Card>
  );
}
