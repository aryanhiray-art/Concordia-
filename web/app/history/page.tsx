"use client";

// /history — two tabs, one page (owner feedback: "History should show the
// history of the fund… the unique part should show their votes"):
//   · Club history (default) — the fund's cycle-by-cycle record, plus the
//     existing latest-run execution detail expanded up top.
//   · Your votes — the caller's own ballots, hits highlighted.
// Tab state is client-side only — no route change.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ClubHistorySession, ClubHistoryView, ExecutionRow, MyVoteSession, MyVotesView } from "@/lib/types";
import { Card, Chip, Dots, LoadingScreen, Pill } from "@/components/ui";

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

// Session status pill: executed = teal · closed = amber (sell settled, buy
// pending) · anything else (open / future states) = neutral.
function sessionTone(s: string): "teal" | "warn" | "neutral" {
  if (s === "executed") return "teal";
  if (s === "closed") return "warn";
  return "neutral";
}

// Render order counts in lifecycle order; unknown statuses trail alphabetically.
const ORDER_STATUS_ORDER = ["placed", "planned", "reviewed", "skipped", "error"];
function orderedCounts(orders: Record<string, number>): [string, number][] {
  return Object.entries(orders).sort(([a], [b]) => {
    const ia = ORDER_STATUS_ORDER.indexOf(a);
    const ib = ORDER_STATUS_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

// A pick chip for the "Your votes" tab: hits (made the basket) keep the teal
// chip language, misses go quiet — same shape, neutral hairline.
function VoteChip({ symbol, weight, hit }: { symbol: string; weight: number; hit: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[12.5px] font-semibold ${
      hit ? "border-teal/40 bg-teal/[0.1] text-ink" : "border-white/10 bg-white/[0.04] text-muted"
    }`}>
      {symbol} <span className="tnum">{weight}%</span>
      {hit && <span className="text-[10.5px] font-medium text-teal">✓ in basket</span>}
    </span>
  );
}

function ClubCycleRow({ s }: { s: ClubHistorySession }) {
  const counts = orderedCounts(s.orders ?? {});
  // status_display is presentation-only: stale closed cycles arrive as
  // "executed"; only the latest closed row keeps "closed" (and the note).
  const shown = s.status_display ?? s.status;
  return (
    <div className="border-t border-hair py-4 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="font-display text-[13.5px] font-semibold tnum">{s.trade_date}</span>
        <Pill tone={sessionTone(shown)}>{shown}</Pill>
        {shown === "closed" && (
          <span className="text-[11px] text-amber/80">sell settled, buy pending</span>
        )}
        <span className="ml-auto whitespace-nowrap text-[12px] text-muted tnum">
          {s.voters} voter{s.voters === 1 ? "" : "s"} ({s.human_voters} human)
        </span>
      </div>
      {s.basket.length > 0 ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {s.basket.map((sym) => <Chip key={sym}>{sym}</Chip>)}
        </div>
      ) : (
        <p className="mt-2 text-[12px] text-subtle">no basket for this cycle</p>
      )}
      {counts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-subtle tnum">
          {counts.map(([st, n]) => (
            <span key={st} className={st === "error" && n > 0 ? "font-semibold text-red" : ""}>
              {n} {st}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function VoteCard({ s }: { s: MyVoteSession }) {
  const open = s.status === "open";
  const shown = s.status_display ?? s.status; // same contract as ClubCycleRow
  const hits = new Set(s.hits ?? []);
  const allocs = [...(s.my_allocations ?? [])].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="font-display text-[13.5px] font-semibold tnum">{s.trade_date}</span>
        <Pill tone={open ? "neutral" : sessionTone(shown)}>{open ? "ballot open" : shown}</Pill>
        {open && <span className="text-[11px] text-subtle">results after close</span>}
      </div>
      {allocs.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {allocs.map((a) => (
            <VoteChip key={a.symbol} symbol={a.symbol} weight={a.weight} hit={!open && hits.has(a.symbol)} />
          ))}
        </div>
      ) : (
        <p className="mt-3 text-[12px] text-subtle">no picks on this ballot</p>
      )}
    </Card>
  );
}

export default function HistoryPage() {
  const { member } = useAuth();
  const [tab, setTab] = useState<"club" | "mine">("club");
  const [data, setData] = useState<ResultsView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hist, setHist] = useState<ClubHistoryView | null>(null);
  const [histErr, setHistErr] = useState<string | null>(null);
  const [mine, setMine] = useState<MyVotesView | null>(null);
  const [mineErr, setMineErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.results());
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || "could not load the execution log");
    }
    try {
      setHist(await api.clubHistory());
      setHistErr(null);
    } catch (e: any) {
      setHistErr(e?.message || "could not load the club history");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  // "Your votes" loads lazily on first visit to the tab — a ballot log doesn't
  // need the 30s poll the execution view gets.
  useEffect(() => {
    if (tab !== "mine" || mine) return;
    let alive = true;
    api.myVotes()
      .then((v) => { if (alive) { setMine(v); setMineErr(null); } })
      .catch((e: any) => { if (alive) setMineErr(e?.message || "could not load your votes"); });
    return () => { alive = false; };
  }, [tab, mine]);

  if (!data && !err) return <div className="pt-10"><LoadingScreen label="loading the history" /></div>;

  const session = data?.session ?? null;
  const rows = (data?.executions ?? []) as ExecRow[];
  const sorted = [...rows].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0) || a.symbol.localeCompare(b.symbol));
  const picks = [...(data?.last_decided?.picks ?? [])].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  // The owner's log includes every member's rows — show whose they are.
  const showMember = member?.role === "owner" && sorted.some((r) => r.display_name);
  const cycles = hist?.sessions ?? [];
  const ballots = mine?.sessions ?? [];

  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">History</h1>
        <p className="mt-2 max-w-[620px] text-[13px] leading-relaxed text-muted">
          The fund&apos;s record — and your own ballots.
        </p>
      </header>

      <div className="seg enter" role="tablist" aria-label="History view">
        <button role="tab" aria-selected={tab === "club"} className={tab === "club" ? "on" : ""} onClick={() => setTab("club")}>
          Club history
        </button>
        <button role="tab" aria-selected={tab === "mine"} className={tab === "mine" ? "on" : ""} onClick={() => setTab("mine")}>
          Your votes
        </button>
      </div>

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

      {tab === "club" && (
        <>
          {/* ── Latest run (the expanded execution detail) ─────────────── */}
          {data && (
            <>
              <div className="enter label !text-teal">Latest run</div>

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

          {/* ── Every past cycle, newest first ─────────────────────────── */}
          <Card>
            <div className="mb-4 flex items-center justify-between gap-3">
              <span className="label">Past cycles</span>
              {cycles.length > 0 && (
                <span className="text-[11.5px] text-subtle tnum">last {cycles.length}</span>
              )}
            </div>
            {histErr && !hist && <div className="text-[13px] text-red">⚠ {histErr}</div>}
            {!hist && !histErr && (
              <div className="flex items-center gap-3 text-[12px] text-subtle"><Dots /> loading the club history…</div>
            )}
            {hist && (cycles.length === 0 ? (
              <p className="text-[13px] text-subtle">No cycles yet — history fills in after the first vote closes.</p>
            ) : (
              <div>
                {cycles.map((s) => <ClubCycleRow key={s.id} s={s} />)}
              </div>
            ))}
          </Card>
        </>
      )}

      {tab === "mine" && (
        <>
          {mineErr && !mine && (
            <Card><div className="text-[13px] text-red">⚠ {mineErr}</div></Card>
          )}
          {!mine && !mineErr && (
            <Card>
              <div className="flex items-center gap-3 text-[12px] text-subtle"><Dots /> loading your ballots…</div>
            </Card>
          )}
          {mine && (ballots.length === 0 ? (
            <Card>
              <p className="text-[13px] text-subtle">
                You haven&apos;t voted yet — today&apos;s ballot is on the{" "}
                <Link href="/vote" className="font-semibold text-teal hover:underline">Vote page</Link>.
              </p>
            </Card>
          ) : (
            ballots.map((s) => <VoteCard key={s.id} s={s} />)
          ))}
        </>
      )}
    </div>
  );
}
