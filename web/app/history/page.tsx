"use client";

// /history — two tabs, one page (owner feedback: "History should show the
// history of the fund… the unique part should show their votes"):
//   · Club history (default) — the fund's cycle-by-cycle record, led by the
//     basket it is holding right now. (The per-member execution table that used
//     to sit here is gone — owner call; the club record is the point.)
//   · Your votes — the caller's own ballots, hits highlighted.
// Tab state is client-side only — no route change.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ClubHistorySession, ClubHistoryView, MyVoteSession, MyVotesView } from "@/lib/types";
import { Card, Dots, LoadingScreen, Pill } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { StockLink } from "@/components/StockLink";
import { fmtTradeDate } from "@/lib/format";

type ResultsView = Awaited<ReturnType<typeof api.results>>;

// Session status pill: executed = teal · closed = amber (sell settled, buy
// pending) · expired = amber (abandoned ballot) · anything else = neutral.
function sessionTone(s: string): "teal" | "warn" | "neutral" {
  if (s === "executed") return "teal";
  if (s === "closed" || s === "expired") return "warn";
  return "neutral";
}

// A pick chip for the "Your votes" tab: hits (made the basket) keep the teal
// chip language, misses go quiet — same shape, neutral hairline. Clicking one
// opens the stock detail panel (StockLink unstyled keeps this custom skin).
function VoteChip({ symbol, weight, hit }: { symbol: string; weight: number; hit: boolean }) {
  return (
    <StockLink symbol={symbol} unstyled
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[12.5px] font-semibold transition ${
        hit ? "border-teal/40 bg-teal/[0.1] text-ink hover:bg-teal/[0.16]"
            : "border-white/10 bg-white/[0.04] text-muted hover:border-white/20 hover:text-ink"
      }`}>
      {symbol} <span className="tnum">{weight}%</span>
      {hit && <span className="text-[10.5px] font-medium text-teal">✓ in basket</span>}
    </StockLink>
  );
}

// The clean club record: date · status chip · the basket. Deliberately NOTHING
// operational here (voter counts, order tallies) — that detail is the admin
// console's Cycles page, and the backend no longer even sends it to members.
function ClubCycleRow({ s }: { s: ClubHistorySession }) {
  // status_display is presentation-only: stale closed cycles arrive as
  // "executed"; only the latest closed row keeps "closed" (and the note).
  const shown = s.status_display ?? s.status;
  return (
    <div className="border-t border-hair py-4 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="font-display text-[13.5px] font-semibold tnum">{fmtTradeDate(s.trade_date)}</span>
        <Pill tone={sessionTone(shown)}>{shown}</Pill>
        {shown === "closed" && (
          <span className="text-[11px] text-amber/80">sell settled, buy pending</span>
        )}
        {/* A cycle that decided nothing gets a note ON the header row instead
            of a second line of its own. As a block it was the same height as a
            real basket, so a run of them read as the list's main content and
            buried the cycles that actually traded. */}
        {s.basket.length === 0 && (
          <span className="text-[11px] text-subtle">no basket decided</span>
        )}
      </div>
      {s.basket.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {s.basket.map((sym) => <StockLink key={sym} symbol={sym} />)}
        </div>
      )}
    </div>
  );
}

function VoteCard({ s }: { s: MyVoteSession }) {
  // Gate on status_display, never the raw status: a ballot abandoned mid-cycle
  // still says 'open' in the DB forever, and promising "results after close"
  // for a cycle that closed a month ago (or never will) is a lie.
  const shown = s.status_display ?? s.status; // same contract as ClubCycleRow
  const open = shown === "open";
  const expired = shown === "expired";
  const hits = new Set(s.hits ?? []);
  const allocs = [...(s.my_allocations ?? [])].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="font-display text-[13.5px] font-semibold tnum">{fmtTradeDate(s.trade_date)}</span>
        <Pill tone={sessionTone(shown)}>{open ? "ballot open" : shown}</Pill>
        {open && <span className="text-[11px] text-subtle">results after close</span>}
        {expired && <span className="text-[11px] text-amber/80">never closed</span>}
      </div>
      {allocs.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {allocs.map((a) => (
            <VoteChip key={a.symbol} symbol={a.symbol} weight={a.weight} hit={hits.has(a.symbol)} />
          ))}
        </div>
      ) : (
        <p className="mt-3 text-[12px] text-subtle">no picks</p>
      )}
    </Card>
  );
}

export default function HistoryPage() {
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
      setErr(e?.message || "could not load the latest basket");
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

  const cycles = hist?.sessions ?? [];
  // The latest decided basket comes from CLUB HISTORY, not /vote/results'
  // last_decided. On the demo instance the club record is mirrored from the
  // real club while the local database holds no decided sessions at all, so
  // last_decided was null there and this card rendered empty under a list of
  // real cycles. Club history is the same source as the cycles below it, so the
  // two can no longer disagree.
  const latest = cycles.find((c) => (c.basket?.length ?? 0) > 0) ?? null;
  const picks = (latest?.basket ?? []).map((symbol) => ({ symbol, name: symbol, weight: undefined as number | undefined }));
  const ballots = mine?.sessions ?? [];

  return (
    <div className="stagger space-y-5">
      <PageHeader title="History" />

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
        <div className="text-[12px] text-subtle">⚠ refresh failed — showing older data</div>
      )}

      {tab === "club" && (
        <>
          {/* ── The basket the club is holding right now ───────────────── */}
          {data && (
            <Card>
              <div className="flex items-center justify-between gap-3">
                <span className="label !text-teal">Latest decided basket</span>
                {latest && (
                  <span className="text-[11.5px] text-subtle tnum">{fmtTradeDate(latest.trade_date)}</span>
                )}
              </div>
              {picks.length > 0 ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {picks.map((p) => <StockLink key={p.symbol} symbol={p.symbol} />)}
                </div>
              ) : (
                <p className="mt-4 text-[13px] text-subtle">No decided basket yet.</p>
              )}
            </Card>
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
              <div className="flex items-center gap-3 text-[12px] text-subtle"><Dots /> loading…</div>
            )}
            {hist && (cycles.length === 0 ? (
              <p className="text-[13px] text-subtle">No cycles yet.</p>
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
              <div className="flex items-center gap-3 text-[12px] text-subtle"><Dots /> loading…</div>
            </Card>
          )}
          {mine && (ballots.length === 0 ? (
            <Card>
              <p className="text-[13px] text-subtle">
                No ballots yet — this cycle&apos;s is on the{" "}
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
