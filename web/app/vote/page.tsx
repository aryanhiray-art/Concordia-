"use client";

// /vote — the one screen where a member does something: find a name, put power
// on it, watch the club's consensus form next to it. The standalone /stocks tab
// folded in here (the search sits inside the ballot card, see StockSearch); the
// research surfaces themselves — the slide-over and /stocks/[symbol] — are
// unchanged and every symbol on this page still opens one.

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import type { SessionView } from "@/lib/types";
import { AllocSliders } from "@/components/AllocSliders";
import { Button, Card, Countdown, LoadingScreen, Pill } from "@/components/ui";
import { StockLink } from "@/components/StockLink";
import { fmtTime, fmtTradeDate } from "@/lib/format";

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
  const total = picks.reduce((a, p) => a + (p.weight || 0), 0);
  return (
    <div className="glass rise p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="label">Your ballot</span>
        <span className="text-[11.5px] text-subtle">read-only · locked {fmtTime(session.closes_at)}</span>
      </div>
      <div className="bar mt-2.5"><i style={{ width: `${Math.min(100, Math.round(total))}%` }} /></div>
      {picks.length === 0 ? (
        <p className="py-7 text-center text-[13px] text-subtle">You didn&apos;t cast a vote this cycle.</p>
      ) : (
        <div className="mt-1 max-h-[min(44vh,380px)] overflow-y-auto">
          {picks.map((p) => (
            <div key={p.symbol} className="vrow">
              <div className="vtk">
                <StockLink symbol={p.symbol} unstyled className="text-left transition hover:text-teal">
                  <b>{p.symbol}</b>
                </StockLink>
                <s>{names[p.symbol] || p.symbol}</s>
              </div>
              <div className="bar min-w-0 flex-1"><i style={{ width: `${Math.min(100, p.weight)}%` }} /></div>
              <span className="w-11 flex-none text-right text-[13px] font-semibold tnum">{Math.round(p.weight)}%</span>
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
          <Button variant="glass" busy={retrying} onClick={retry} className="mt-4">Retry</Button>
        </Card>
      </div>
    );
  }
  if (!session) return <div className="pt-10"><LoadingScreen label="loading the ballot" /></div>;

  const open = session.status === "open";
  const basket = ((session.picks?.length ? session.picks : session.tally) || []).slice(0, session.top_n);
  const maxW = Math.max(1, ...basket.map((b) => b.weight || 0));
  const mine = new Set((session.my_picks || []).map((p) => p.symbol));
  const anyMine = basket.some((b) => mine.has(b.symbol));

  return (
    <div className="stagger space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="font-display text-[28px] font-bold leading-none tracking-tight sm:text-[34px]">Vote</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
            <span>{fmtTradeDate(session.trade_date)}</span>
            {open
              ? <Pill tone="teal">closes in <b><Countdown to={session.closes_at} /></b></Pill>
              : <Pill tone="danger">voting closed</Pill>}
          </div>
        </div>
        {/* wraps under the title on a phone, so it left-aligns there and only
            hugs the right edge once it shares the row with the title */}
        <div className="text-left sm:text-right">
          <div className="label !text-[10px]">Your voting power</div>
          <div className="mt-1.5 font-display text-[27px] font-bold leading-none tnum sm:text-[31px]">{money(power?.power)}</div>
        </div>
      </header>

      {/* [&>*]:min-w-0 is load-bearing, and the sibling of the min-width:0 note
          on .slider in globals.css: a grid item's automatic minimum size is its
          MIN-CONTENT, and a range input's is ~129px, so on a 375px phone the
          ballot card grew 33px past the column and only html{overflow-x:hidden}
          hid it. Zeroing the items' minimum lets the slider shrink instead. */}
      <div className="grid items-start gap-5 [&>*]:min-w-0 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* three distinct states: participation actually OFF → AllocSliders'
            turn-it-on-in-Account message; ON but the ballot closed → the
            read-only locked ballot; ON and open → the live ballot. */}
        {participating && !open
          ? <ClosedBallot session={session} />
          : <AllocSliders session={session} participating={participating} onVoted={() => refreshSession()} />}

        <div className="space-y-3">
          {/* forming basket — power-weighted consensus */}
          <div className="glass shine rise p-4 sm:p-5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="label !text-teal">Club consensus</span>
              <span className="text-[11.5px] text-subtle">
                {session.voters} voter{session.voters === 1 ? "" : "s"} · top {session.top_n}
              </span>
            </div>
            {basket.length === 0 ? (
              <p className="py-7 text-center text-[13px] text-subtle">No votes yet.</p>
            ) : (
              <div className="mt-3.5 flex max-h-[min(46vh,400px)] flex-col gap-2.5 overflow-y-auto">
                {basket.map((b) => (
                  <div key={b.symbol} className="flex items-center gap-2.5">
                    <StockLink symbol={b.symbol} unstyled
                      className={`w-14 flex-none text-left font-display text-[13px] font-semibold transition hover:text-teal ${mine.has(b.symbol) ? "text-teal" : ""}`}>
                      {b.symbol}
                    </StockLink>
                    <div className="bar min-w-0 flex-1"><i style={{ width: `${((b.weight || 0) / maxW) * 100}%` }} /></div>
                    <span className="w-14 flex-none text-right text-[12px] tnum text-muted">{money(b.weight)}</span>
                  </div>
                ))}
              </div>
            )}
            {anyMine && <p className="mt-3 text-[11px] text-subtle"><span className="text-teal">teal</span> = on your ballot</p>}
          </div>

        </div>
      </div>
    </div>
  );
}
