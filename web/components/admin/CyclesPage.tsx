"use client";

// /cycles — the per-cycle operational record. This is the detail the MEMBER
// club history deliberately no longer shows (voter counts, human-vs-bot split,
// per-status order tallies): members get the clean fund record, operators get
// the truth here. One row per decided session, newest first; clicking a row
// expands the per-member drilldown — placed/deferred counts, the deferred
// symbols + dollar amounts, skip and error reasons.
//
// "deferred" is an HONEST relabel done at aggregation time by the backend
// (executions rows are recorded as status='skipped' with a "deferred — beyond
// settled buying power" error and are never rewritten) — a settled-cash defer
// is a buy waiting for proceeds, not a real skip.
//
// Degrades: a backend that predates /api/admin/cycles 404s → one dim note
// (same contract as the Runs page — a web deploy can precede the backend).

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AdminCycleMember, type AdminCycleRow } from "@/lib/api";
import { Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { OpsCard, SectionHead, SectionLabel, TH_FONT as TH } from "./util";

function StatusChip({ c }: { c: AdminCycleRow }) {
  const shown = c.status_display ?? c.status;
  const tone = shown === "executed" ? "teal" : shown === "closed" ? "warn" : "neutral";
  return <Pill tone={tone}>{shown}</Pill>;
}

/** placed / deferred / skipped / error as one mono scan cell. Any status the
 *  backend grows beyond the four buckets renders verbatim after them. */
function OrderCounts({ orders }: { orders: Record<string, number> }) {
  const known = ["placed", "deferred", "skipped", "error"] as const;
  const cls: Record<string, string> = {
    placed: "font-semibold text-green", deferred: "text-amber",
    skipped: "text-amber", error: "font-semibold text-red",
  };
  const extras = Object.entries(orders).filter(([k]) => !known.includes(k as never));
  return (
    <span className="whitespace-nowrap font-mono text-[11.5px] tnum">
      {known.map((k, i) => {
        const n = orders[k] ?? 0;
        return (
          <span key={k}>
            {i > 0 && <span className="text-subtle"> / </span>}
            <span className={n ? cls[k] : "text-subtle"}>{n}</span>
          </span>
        );
      })}
      {extras.map(([k, n]) => (
        <span key={k} className="ml-1.5 text-subtle">{n} {k}</span>
      ))}
    </span>
  );
}

function fmtAmt(n: number | null | undefined): string {
  return n == null ? "—" : `$${n.toFixed(2)}`;
}

/** One member's drilldown line: counts up front, then the receipts — the
 *  deferred symbols + amounts and every skip/error reason. */
function MemberRow({ m }: { m: AdminCycleMember }) {
  return (
    <div className="border-t border-hair py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[12.5px] font-semibold">{m.name || `member ${m.member_id}`}</span>
        <span className="font-mono text-[11.5px] text-muted tnum">
          <span className={m.placed ? "font-semibold text-green" : "text-subtle"}>{m.placed} placed</span>
          <span className="text-subtle"> · </span>
          <span className={m.deferred ? "text-amber" : "text-subtle"}>{m.deferred} deferred</span>
          <span className="text-subtle"> · </span>
          <span className={m.skipped ? "text-amber" : "text-subtle"}>{m.skipped} skipped</span>
          <span className="text-subtle"> · </span>
          <span className={m.errors ? "font-semibold text-red" : "text-subtle"}>{m.errors} error{m.errors === 1 ? "" : "s"}</span>
        </span>
      </div>
      {m.deferred_orders.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px]">
          <span className="text-subtle">deferred:</span>
          {m.deferred_orders.map((o, i) => (
            <span key={i} className="rounded-[7px] border border-amber/25 bg-amber/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-amber">
              {o.symbol} {fmtAmt(o.amount)}
            </span>
          ))}
        </div>
      )}
      {m.skips.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[11.5px] text-subtle">
          {m.skips.map((s, i) => (
            <li key={i}>
              <span className="font-mono text-[11px] text-muted">{s.symbol}</span>
              {" "}skipped — {s.reason || "no reason recorded"}
            </li>
          ))}
        </ul>
      )}
      {m.error_orders.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[11.5px] text-red/90">
          {m.error_orders.map((e, i) => (
            <li key={i}>
              <span className="font-mono text-[11px]">{e.symbol}</span>
              {" "}error — {e.reason || "no error recorded"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CyclesPage() {
  const [cycles, setCycles] = useState<AdminCycleRow[] | null>(null);
  const [missing, setMissing] = useState(false);     // endpoint 404 = older backend
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.adminCycles();
      setCycles(r.cycles);
      setErr(null);
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <OpsCard>
      <SectionHead label="Cycle operations"
        right={cycles?.length
          ? <span className="font-mono text-[11px] tnum text-subtle">{cycles.length} cycles</span>
          : undefined} />
      <p className="mb-3 text-[11.5px] text-subtle">
        the operational half of the club record — members see date · status ·
        basket only; the voter split and order outcomes live here
      </p>

      {missing ? (
        <p className="text-[12px] text-subtle">
          cycle operations unavailable — this backend predates /api/admin/cycles.
          Deploy the new backend to see the voters split and order outcomes here.
        </p>
      ) : err && !cycles ? (
        <p className="text-[12.5px] text-red">⚠ {err}</p>
      ) : !cycles ? (
        <p className="text-[12px] text-subtle">loading cycles…</p>
      ) : cycles.length === 0 ? (
        <p className="text-[12px] text-subtle">no decided cycles yet</p>
      ) : (
        <>
          {err && <p className="mb-2 text-[12px] text-red">⚠ {err} — showing loaded rows</p>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-[12.5px]">
              <thead>
                <tr>
                  <th className={`${TH} pr-3 text-left`}>cycle</th>
                  <th className={`${TH} pr-3 text-left`}>status</th>
                  <th className={`${TH} pr-3 text-left`}>basket</th>
                  <th className={`${TH} pr-3 text-right`} title="human voters + bot voters">voters</th>
                  <th className={`${TH} text-right`} title="placed / deferred / skipped / error — this cycle's member orders">p / d / s / e</th>
                </tr>
              </thead>
              <tbody>
                {cycles.map((c) => {
                  const on = expanded === c.id;
                  return [
                    <tr key={c.id}
                      onClick={() => setExpanded(on ? null : c.id)}
                      aria-expanded={on}
                      className={`cursor-pointer border-t border-hair transition hover:bg-white/[0.025] ${on ? "bg-white/[0.02]" : ""}`}>
                      <td className="whitespace-nowrap py-1.5 pr-3">
                        <span className="flex items-center gap-1.5">
                          <span className={`inline-block w-2 flex-none text-[10px] text-subtle transition-transform ${on ? "rotate-90" : ""}`} aria-hidden>▸</span>
                          <span className="font-semibold tnum">{fmtTradeDate(c.trade_date)}</span>
                        </span>
                      </td>
                      <td className="py-1.5 pr-3"><StatusChip c={c} /></td>
                      <td className="py-1.5 pr-3">
                        <span className="flex flex-wrap gap-1">
                          {c.basket.length === 0
                            ? <span className="text-subtle">—</span>
                            : c.basket.map((s) => (
                              <span key={s} className="rounded-[7px] border border-teal/25 bg-teal/[0.06] px-1.5 py-0.5 font-mono text-[11px]">{s}</span>
                            ))}
                        </span>
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-3 text-right font-mono text-[11.5px] tnum">
                        <span className="font-semibold">{c.voters.humans}</span>
                        <span className="text-subtle"> human{c.voters.humans === 1 ? "" : "s"}</span>
                        <span className="text-subtle"> + </span>
                        <span className={c.voters.bots ? "text-amber" : "text-subtle"}>{c.voters.bots}</span>
                        <span className="text-subtle"> bot{c.voters.bots === 1 ? "" : "s"}</span>
                      </td>
                      <td className="py-1.5 text-right"><OrderCounts orders={c.orders} /></td>
                    </tr>,
                    on ? (
                      <tr key={`${c.id}-detail`} className="border-t border-hair bg-white/[0.015]">
                        <td colSpan={5} className="px-2 pb-2 sm:px-4">
                          <div className="py-1">
                            <SectionLabel>Member orders</SectionLabel>
                            {c.members.length === 0 ? (
                              <div className="text-[12px] text-subtle">no execution rows recorded for this cycle</div>
                            ) : (
                              c.members.map((m) => <MemberRow key={m.member_id} m={m} />)
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null,
                  ];
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </OpsCard>
  );
}
