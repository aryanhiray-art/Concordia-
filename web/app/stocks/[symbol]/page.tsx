"use client";

// /stocks/[symbol] — the RESEARCH view. Same payload as the slide-over
// (GET /api/stocks/{sym}), a deeper presentation: a large interactive chart,
// where the price sits inside its day and 52-week ranges, volume against its
// average, the full fundamentals grid, and the add-to-ballot control.
//
// The panel remains the surface for symbols met outside research (history
// chips, tally rows, holdings) — see components/StockPanel.tsx.

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { StockDetail, StockRangeKey } from "@/lib/types";
import { fmtBig, fmtPrice, fmtRatio, pctInRange } from "@/lib/stocknum";
import { fmtTradeDate } from "@/lib/format";
import { AddToBallot } from "@/components/AddToBallot";
import { PriceChart } from "@/components/PriceChart";
import { Card, Freshness, Pill } from "@/components/ui";

const RANGES: StockRangeKey[] = ["1M", "3M", "1Y"];

/** Where the current price sits between two bounds. Renders nothing but a dash
 *  when either bound is missing or the span is degenerate (pctInRange → null). */
function RangeBar({
  label, lo, hi, value, note,
}: { label: string; lo: number | null; hi: number | null; value: number | null; note?: string }) {
  const pct = pctInRange(value, lo, hi);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="label">{label}</span>
        {note && <span className="text-[11.5px] text-subtle tnum">{note}</span>}
      </div>
      {pct == null ? (
        <div className="mt-2 text-[13px] text-subtle">—</div>
      ) : (
        <>
          <div className="relative mt-2.5 h-[9px] rounded-[6px] bg-white/[0.06]">
            <i className="absolute inset-y-0 left-0 block rounded-[6px] bg-gradient-to-r from-teal-deep to-teal"
              style={{ width: `${pct}%` }} />
            <i className="absolute -top-[3px] h-[15px] w-[2px] rounded-full bg-ink shadow-[0_0_8px_rgba(45,212,191,0.9)]"
              style={{ left: `calc(${pct}% - 1px)` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-[11.5px] text-subtle tnum">
            <span>{fmtPrice(lo)}</span><span>{fmtPrice(hi)}</span>
          </div>
        </>
      )}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-5" aria-hidden>
      <div className="pulse h-10 w-56 rounded-lg bg-white/[0.07]" />
      <div className="pulse h-[360px] rounded-[18px] bg-white/[0.05]" />
      <div className="grid gap-5 md:grid-cols-2">
        <div className="pulse h-52 rounded-[18px] bg-white/[0.05]" />
        <div className="pulse h-52 rounded-[18px] bg-white/[0.05]" />
      </div>
    </div>
  );
}

export default function StockPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol || "").trim().toUpperCase();
  const [data, setData] = useState<StockDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const [range, setRange] = useState<StockRangeKey>("3M");

  useEffect(() => {
    let alive = true;
    setData(null); setState("loading"); setErr(null); setRange("3M");
    api.stockDetail(symbol)
      .then((d) => { if (alive) { setData(d); setState("ready"); } })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) setState("missing");
        else { setState("error"); setErr(e instanceof Error ? e.message : "couldn't load this stock"); }
      });
    return () => { alive = false; };
  }, [symbol]);

  const q = data?.quote;
  const f = data?.fundamentals;
  const day = data?.day;
  const up = (q?.change_pct ?? 0) >= 0;
  const price = q?.price ?? null;
  const vol = f?.volume ?? day?.volume ?? null;
  const avgVol = f?.avg_volume ?? null;
  const volRatio = vol != null && avgVol ? vol / avgVol : null;

  const facts: [string, string][] = [
    ["Mkt cap", fmtBig(f?.market_cap)],
    ["P/E", fmtRatio(f?.pe_ratio)],
    ["Div yield", f?.dividend_yield != null ? `${f.dividend_yield.toFixed(2)}%` : "—"],
    ["Sector", f?.sector || "—"],
    ["52w high", fmtPrice(f?.high_52w)],
    ["52w low", fmtPrice(f?.low_52w)],
    ["Open", fmtPrice(day?.open)],
    ["Prev close", fmtPrice(q?.prev_close)],
    ["Volume", fmtBig(vol)],
    ["Avg vol", fmtBig(avgVol)],
  ];

  return (
    <div className="space-y-6">
      <Link href="/stocks" className="enter inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-muted transition hover:text-teal">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
        Stocks
      </Link>

      <header className="enter flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">{symbol}</h1>
            {data?.in_universe && <Pill tone="teal">votable</Pill>}
          </div>
          {data && <div className="mt-2 truncate text-[13.5px] text-muted">{data.name}</div>}
        </div>
        {state === "ready" && data && <AddToBallot symbol={symbol} inUniverse={!!data.in_universe} />}
      </header>

      {state === "loading" && <Skeleton />}

      {state === "missing" && (
        <Card className="enter text-center">
          <div className="font-display text-[16px] font-bold">No such symbol</div>
          <p className="mt-2 text-[13px] text-subtle">
            Not in the club universe, and the broker doesn&apos;t price it.
          </p>
        </Card>
      )}

      {state === "error" && (
        <div className="rounded-[14px] border border-red/30 bg-red/[0.07] p-4 text-[13px] text-red">
          ⚠ Couldn&apos;t load {symbol} — {err}
        </div>
      )}

      {state === "ready" && data && (
        <div className="stagger space-y-5">
          <Card>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <div className="hero-num tnum">{fmtPrice(price)}</div>
                {q?.change_pct != null && (
                  <div className={`mt-2 text-[14px] font-semibold tnum ${up ? "text-teal" : "text-red"}`}>
                    {up ? "▲" : "▼"} {up ? "+" : ""}{q.change_pct.toFixed(2)}%
                    {q.prev_close != null && (
                      <span className="ml-2.5 font-medium text-subtle">prev close {fmtPrice(q.prev_close)}</span>
                    )}
                  </div>
                )}
              </div>
              <div className="flex gap-1.5">
                {RANGES.map((r) => (
                  <button key={r} className={`tf ${r === range ? "on" : ""}`} onClick={() => setRange(r)}>{r}</button>
                ))}
              </div>
            </div>
            <div className="mt-4">
              <PriceChart points={data.range?.[range] ?? []} height={300} interactive />
            </div>
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card className="space-y-5">
              <RangeBar label="Day range" lo={day?.low ?? null} hi={day?.high ?? null} value={price}
                note={day?.d ? fmtTradeDate(day.d) : undefined} />
              <RangeBar label="52-week range" lo={f?.low_52w ?? null} hi={f?.high_52w ?? null} value={price} />
              <div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="label">Volume vs average</span>
                  <span className="text-[11.5px] text-subtle tnum">
                    {volRatio != null ? `${volRatio.toFixed(2)}× avg` : "—"}
                  </span>
                </div>
                {volRatio == null ? (
                  <div className="mt-2 text-[13px] text-subtle">—</div>
                ) : (
                  <>
                    {/* the average sits at the halfway mark, so 2× fills the track */}
                    <div className="relative mt-2.5 h-[9px] rounded-[6px] bg-white/[0.06]">
                      <i className="absolute inset-y-0 left-0 block rounded-[6px] bg-gradient-to-r from-teal-deep to-teal"
                        style={{ width: `${Math.min(100, volRatio * 50)}%` }} />
                      <i className="absolute -top-[3px] left-1/2 h-[15px] w-px bg-white/30" />
                    </div>
                    <div className="mt-1.5 flex justify-between text-[11.5px] text-subtle tnum">
                      <span>{fmtBig(vol)}</span><span>avg {fmtBig(avgVol)}</span>
                    </div>
                  </>
                )}
              </div>
            </Card>

            <Card>
              <div className="label mb-3">Fundamentals</div>
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-2 xl:grid-cols-3">
                {facts.map(([k, v]) => (
                  <div key={k} className="rounded-[11px] border border-hair bg-white/[0.03] px-3 py-2.5">
                    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{k}</div>
                    <div className="mt-1 truncate font-display text-[14px] font-semibold tnum" title={v}>{v}</div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          {f?.description && (
            <Card>
              <div className="label mb-2">About</div>
              <p className="text-[13px] leading-relaxed text-muted">{f.description}</p>
            </Card>
          )}

          <Freshness asOf={data.as_of ?? null} />
        </div>
      )}
    </div>
  );
}
