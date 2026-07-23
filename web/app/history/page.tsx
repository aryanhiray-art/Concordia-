"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ExecutionRow } from "@/lib/types";
import { Card, Chip, LoadingScreen, Pill } from "@/components/ui";

// The backend returns the raw executions row (SELECT *), which carries more
// than the shared ExecutionRow type declares — the fields we render extra:
type ExecRow = ExecutionRow & {
  id?: number;
  created_at?: number;      // epoch seconds
  place_json?: string | null;
};

type ResultsView = Awaited<ReturnType<typeof api.results>>;

function money(n?: number | null) {
  if (n == null) return null;
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

// Member's local time with timezone abbreviation, e.g. "Jul 23, 9:31 AM EDT".
function when(ts?: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
}

// Sell orders carry no dollar_amount — the estimated share quantity lives in place_json.
function qtyOf(r: ExecRow): string | null {
  if (!r.place_json) return null;
  try {
    const q = JSON.parse(r.place_json)?._concord_est_qty;
    if (typeof q === "number" && Number.isFinite(q)) return `${q} sh`;
  } catch { /* ignore malformed json */ }
  return null;
}

// placed = teal · held/reviewed/planned = muted · skipped = amber · error = red
function statusTone(s: string): "teal" | "warn" | "danger" | "neutral" {
  if (s === "placed") return "teal";
  if (s === "skipped") return "warn";
  if (s === "error") return "danger";
  return "neutral";
}

export default function HistoryPage() {
  const { member } = useAuth();
  const [data, setData] = useState<ResultsView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.results());
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || "could not load the execution log");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  if (!data && !err) return <div className="pt-10"><LoadingScreen label="loading the execution log" /></div>;

  const session = data?.session ?? null;
  const rows = (data?.executions ?? []) as ExecRow[];
  const sorted = [...rows].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0) || a.symbol.localeCompare(b.symbol));
  const picks = [...(data?.last_decided?.picks ?? [])].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  // The owner's log includes every member's rows — show whose they are.
  const showMember = member?.role === "owner" && sorted.some((r) => r.display_name);

  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">History</h1>
        <p className="mt-2 max-w-[620px] text-[13px] leading-relaxed text-muted">
          Every order the club has placed in YOUR account for the current cycle — statuses come
          straight from the execution log.
        </p>
      </header>

      {/* a failed BACKGROUND refresh keeps the loaded data on screen — the
          error card replaces content only when there is nothing to show yet */}
      {err && !data && (
        <Card>
          <div className="text-[13px] text-red">⚠ {err}</div>
        </Card>
      )}
      {err && data && (
        <div className="text-[12px] text-subtle">⚠ last refresh failed — showing the previous data</div>
      )}

      {data && (
        <>
          {/* ── The latest decided basket ─────────────────────────────── */}
          <Card>
            <div className="flex items-center justify-between gap-3">
              <span className="label !text-teal">Latest decided basket</span>
              {data?.last_decided && (
                <span className="text-[11.5px] text-subtle tnum">{data.last_decided.trade_date}</span>
              )}
            </div>
            {picks.length > 0 ? (
              <div className="mt-4 flex flex-wrap gap-2">
                {picks.map((p) => <Chip key={p.symbol}>{p.symbol}</Chip>)}
              </div>
            ) : (
              <p className="mt-4 text-[13px] text-subtle">No decided basket yet — nothing has been voted through.</p>
            )}
          </Card>

          {/* ── Your executions ───────────────────────────────────────── */}
          <Card>
            <div className="flex items-center justify-between gap-3">
              <span className="label">Your executions</span>
              {session && <span className="text-[11.5px] text-subtle tnum">cycle {session.trade_date}</span>}
            </div>

            {sorted.length === 0 ? (
              <p className="mt-4 text-[13px] text-subtle">No orders in your account yet for this basket.</p>
            ) : (
              // the table scrolls inside this container on small screens — never the page
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-left">
                  <thead>
                    <tr>
                      {showMember && <th className="label whitespace-nowrap pb-3 pr-4 !text-[10.5px]">Member</th>}
                      <th className="label whitespace-nowrap pb-3 pr-4 !text-[10.5px]">Symbol</th>
                      <th className="label whitespace-nowrap pb-3 pr-4 !text-[10.5px]">Side</th>
                      <th className="label whitespace-nowrap pb-3 pr-4 !text-[10.5px]">Amount</th>
                      <th className="label whitespace-nowrap pb-3 pr-4 !text-[10.5px]">Status</th>
                      <th className="label whitespace-nowrap pb-3 !text-[10.5px]">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((r, i) => (
                      <tr key={r.id ?? `${r.symbol}-${r.side}-${i}`} className="border-t border-hair align-top">
                        {showMember && (
                          <td className="whitespace-nowrap py-3 pr-4 text-[13px] text-muted">{r.display_name || "—"}</td>
                        )}
                        <td className="whitespace-nowrap py-3 pr-4 font-display text-[13.5px] font-semibold">{r.symbol}</td>
                        <td className={`whitespace-nowrap py-3 pr-4 text-[12.5px] font-medium ${r.side === "sell" ? "text-red" : "text-green"}`}>
                          {r.side}
                        </td>
                        <td className="whitespace-nowrap py-3 pr-4 text-[13px] tnum">
                          {money(r.dollar_amount) ?? qtyOf(r) ?? "—"}
                        </td>
                        <td className="py-3 pr-4">
                          <Pill tone={statusTone(r.status)}>{r.status}</Pill>
                          {r.error && (r.status === "skipped" || r.status === "error") && (
                            <div className={`mt-1.5 max-w-[280px] text-[11.5px] leading-snug ${r.status === "error" ? "text-red" : "text-amber"}`}>
                              {r.error}
                            </div>
                          )}
                        </td>
                        <td className="whitespace-nowrap py-3 text-[12.5px] text-muted tnum">{when(r.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
