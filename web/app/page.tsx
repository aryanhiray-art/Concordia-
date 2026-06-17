"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Account } from "@/lib/types";
import { Onboarding } from "@/components/Onboarding";
import { Countdown } from "@/components/ui";

function money(n?: number | null, dp = 0) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: dp });
}

function Stat({ k, v, s, tone }: { k: string; v: React.ReactNode; s?: string; tone?: string }) {
  return (
    <div className="statcell">
      <div className="k">{k}</div>
      <div className={`v ${tone || ""}`}>{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

export default function Overview() {
  const { member, power, session } = useAuth();
  const [acc, setAcc] = useState<Account | null>(null);

  const connected = member?.robinhood?.status === "connected";
  useEffect(() => { if (connected) api.account().then(setAcc).catch(() => {}); }, [connected, member]);

  const committed = acc?.allocation?.resolved_usd ?? null;
  const pnl = acc?.pnl;
  const bal = acc?.allocation?.balance;
  const up = (pnl?.profit ?? 0) >= 0;

  const open = session?.status === "open";
  const basket = ((session?.picks?.length ? session.picks : session?.tally) || []).slice(0, session?.top_n || 20);
  const maxW = Math.max(1, ...basket.map((b) => b.weight || 0));

  return (
    <div className="stagger space-y-6">
      <Onboarding />

      {/* ── hero ───────────────────────────────────────────────── */}
      <section className="enter pt-2">
        <div className="hero-eyebrow">Your stake in the club</div>
        <div className="hero-num mt-3">{money(committed, committed && committed < 1000 ? 2 : 0)}</div>
        {pnl && pnl.invested > 0 ? (
          <div className="hero-chg mt-2.5">
            <span className={up ? "text-green" : "text-red"}>
              {up ? "▲" : "▼"} {money(Math.abs(pnl.profit), 2)}
              {pnl.profit_pct != null && <> · {pnl.profit_pct >= 0 ? "+" : ""}{pnl.profit_pct}%</>}
            </span>
            <span className="ml-2 text-subtle">club profit · on {money(pnl.invested, 2)} deployed</span>
          </div>
        ) : (
          <div className="mt-2.5 text-[13.5px] text-subtle">
            committed to the club — deployed across the consensus basket each day, and the only thing P&amp;L tracks
          </div>
        )}
      </section>

      {/* ── stat strip ─────────────────────────────────────────── */}
      <div className="statrow enter">
        <Stat k="Voting power" v={money(power?.power)} tone="text-teal"
          s={power?.delta ? `${power.delta >= 0 ? "▲" : "▼"} ${money(Math.abs(power.delta))} wk` : "your say in the basket"} />
        <Stat k="Committed" v={money(committed, committed && committed < 1000 ? 2 : 0)} s="traded + tracked" />
        <Stat k="Club P&L" v={pnl && pnl.invested > 0 ? `${up ? "+" : ""}${money(pnl.profit, 0)}` : "—"}
          tone={pnl && pnl.invested > 0 ? (up ? "text-green" : "text-red") : ""} s="club positions only" />
        <Stat k="Balance" v={money(bal?.total_value)} s={bal ? `${money(bal.cash)} cash` : "connect to see"} />
      </div>

      {/* ── consensus basket ───────────────────────────────────── */}
      <section className="glass enter p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-display text-[16px] font-bold">Today&apos;s consensus basket</h2>
            <p className="mt-0.5 text-[12px] text-subtle">
              top {session?.top_n ?? 20} by <b className="text-teal">voting power</b> · {session?.voters ?? 0} voter{(session?.voters ?? 0) === 1 ? "" : "s"} · {session?.trade_date ?? "—"}
            </p>
          </div>
          {open
            ? <Link href="/vote" className="btn btn-go !px-4 !py-2.5 text-[13px]">Cast your vote →</Link>
            : <span className="pill pill-teal">{session?.status ?? "closed"}</span>}
        </div>

        {basket.length === 0 ? (
          <p className="mt-5 text-[13px] text-subtle">No votes yet today — be the first to allocate.</p>
        ) : (
          <div className="mt-5 flex flex-col gap-3">
            {basket.map((b) => (
              <div key={b.symbol} className="flex items-center gap-3">
                <span className="w-16 flex-none font-display text-[13.5px] font-semibold">{b.symbol}</span>
                <div className="bar flex-1"><i style={{ width: `${((b.weight || 0) / maxW) * 100}%` }} /></div>
                <span className="w-20 flex-none text-right text-[12.5px] tnum text-muted">{money(b.weight)}</span>
              </div>
            ))}
          </div>
        )}

        {open && (
          <div className="mt-4 text-[11.5px] text-subtle">
            voting closes in <b className="text-teal"><Countdown to={session!.closes_at} /></b> · the basket fills at the open
          </div>
        )}
      </section>
    </div>
  );
}
