"use client";

import { useAuth } from "@/lib/auth";
import { AllocSliders } from "@/components/AllocSliders";
import { Countdown, LoadingScreen } from "@/components/ui";

function money(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export default function VotePage() {
  const { member, power, session, refreshSession } = useAuth();
  const participating = Number(member?.settings?.opted_in) === 1;

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
        <AllocSliders session={session} participating={participating && open} onVoted={() => refreshSession()} />

        {/* forming basket — power-weighted consensus */}
        <div className="glass rise p-5 sm:p-6">
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
                  <span className="w-14 flex-none font-display text-[13px] font-semibold">{b.symbol}</span>
                  <div className="bar flex-1"><i style={{ width: `${((b.weight || 0) / maxW) * 100}%` }} /></div>
                  <span className="w-16 flex-none text-right text-[12px] tnum text-muted">{money(b.weight)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
