"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import type { SessionView } from "@/lib/types";
import { AllocSliders } from "@/components/AllocSliders";
import { MemberKeys } from "@/components/MemberKeys";
import { Button, Card, Countdown, LoadingScreen } from "@/components/ui";
import { StockLink } from "@/components/StockLink";

function money(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

// A participating member after close: the ballot is LOCKED, not "participation
// off" — the old message sent them to Account to flip a switch that was already
// on. Shows when the ballot locked plus their submitted picks, read-only.
function ClosedBallot({ session }: { session: SessionView }) {
  const names: Record<string, string> = {};
  session.tally.forEach((t) => { names[t.symbol] = t.name; });
  const picks = session.my_picks || [];
  const lockedAt = new Date(session.closes_at * 1000)
    .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return (
    <div className="glass rise p-5 sm:p-6">
      <div className="flex items-center justify-between border-b border-hair pb-3">
        <span className="label">Your ballot</span>
        <span className="text-[11.5px] text-subtle">read-only</span>
      </div>
      <p className="mt-3 text-[13px] text-muted">
        Voting is closed — today&apos;s ballot locked at <b className="text-ink">{lockedAt}</b>.
      </p>
      {picks.length === 0 ? (
        <p className="mt-3 text-[13px] text-subtle">You didn&apos;t cast a vote this cycle.</p>
      ) : (
        <div className="mt-3">
          {picks.map((p) => (
            <div key={p.symbol} className="vrow">
              <div className="vtk">
                <StockLink symbol={p.symbol} unstyled className="text-left transition hover:text-teal">
                  <b>{p.symbol}</b>
                </StockLink>
                <s>{names[p.symbol] || p.symbol}</s>
              </div>
              <span className="ml-auto text-[13px] font-semibold tnum">{Math.round(p.weight)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function VotePage() {
  const { member, power, session, sessionError, refreshSession } = useAuth();
  const participating = Number(member?.settings?.opted_in) === 1;
  const [retrying, setRetrying] = useState(false);

  // Ballot fetch failed — show an error + retry instead of spinning forever.
  if (!session && sessionError) {
    const retry = async () => {
      setRetrying(true);
      try { await refreshSession(); } finally { setRetrying(false); }
    };
    return (
      <div className="pt-10">
        <Card className="enter mx-auto max-w-[460px]">
          <span className="label">Today&apos;s ballot</span>
          <p className="mt-3 text-[13px] text-red">⚠ Couldn&apos;t load the ballot — {sessionError}</p>
          <p className="mt-1.5 text-[12px] text-subtle">A connection blip — you&apos;re still signed in.</p>
          <Button variant="glass" busy={retrying} onClick={retry} className="mt-4">Retry</Button>
        </Card>
      </div>
    );
  }
  if (!session) return <div className="pt-10"><LoadingScreen label="loading the ballot" /></div>;

  const open = session.status === "open";
  const basket = ((session.picks?.length ? session.picks : session.tally) || []).slice(0, session.top_n);
  const maxW = Math.max(1, ...basket.map((b) => b.weight || 0));

  return (
    <div className="space-y-7">
      <header className="enter flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">Cast your vote</h1>
          <div className="mt-2.5 text-[13.5px] text-muted">
            {session.trade_date} ·{" "}
            {open ? <>voting <b className="text-teal">closes in <Countdown to={session.closes_at} /></b></>
              : <span className="text-red">voting closed</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="label !text-[10px]">Your voting power</div>
          <div className="hero-num sm mt-1.5 tnum">{money(power?.power)}</div>
        </div>
      </header>

      <div className="grid items-start gap-7 lg:grid-cols-[1.55fr_1fr]">
        {/* three distinct states: participation actually OFF → AllocSliders'
            turn-it-on-in-Account message; ON but the ballot closed → the
            read-only locked ballot; ON and open → the live sliders. */}
        {participating && !open
          ? <ClosedBallot session={session} />
          : <AllocSliders session={session} participating={participating} onVoted={() => refreshSession()} />}

        <div className="space-y-7">
          {/* generate your own API key here — let a bot cast this vote for you */}
          <MemberKeys />

          {/* forming basket — power-weighted consensus */}
          <div className="glass shine rise p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <span className="label !text-teal">Forming basket</span>
              <span className="text-[11.5px] text-subtle">{session.voters} voter{session.voters === 1 ? "" : "s"}</span>
            </div>
            <p className="mt-1 text-[12px] text-subtle">top {session.top_n} by voting power</p>
            {basket.length === 0 ? (
              <p className="mt-5 text-[13px] text-subtle">No votes yet — yours could lead.</p>
            ) : (
              <div className="mt-4 flex flex-col gap-2.5">
                {basket.map((b) => (
                  <div key={b.symbol} className="flex items-center gap-2.5">
                    <StockLink symbol={b.symbol} unstyled
                      className="w-14 flex-none text-left font-display text-[13px] font-semibold transition hover:text-teal">
                      {b.symbol}
                    </StockLink>
                    <div className="bar flex-1"><i style={{ width: `${((b.weight || 0) / maxW) * 100}%` }} /></div>
                    <span className="w-16 flex-none text-right text-[12px] tnum text-muted">{money(b.weight)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
