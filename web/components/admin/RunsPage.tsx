"use client";

// /runs — the executor's persisted flight log. One row per invocation (idle
// runs included: a missing record is itself a finding), newest first, with
// "load more" paging on a before=<id> cursor. Clicking a row expands the
// run's full summary — per-member order tables, the skip-reason breakdown,
// the commitment write-downs and carry drains the order table has no row for,
// the verification report, and the raw JSON — so execution forensics never
// need ssh + journalctl again (the 08/19 deferred-buys incident was debugged
// exactly that way).
//
// Degrades: a backend that predates /api/admin/runs 404s → one dim note, the
// page never blanks or errors loudly (a web deploy can precede the backend).

import { useCallback, useEffect, useState } from "react";
import {
  api, ApiError,
  type AdminRunDetail, type AdminRunMember, type AdminRunOrder, type AdminRunRow, type AdminRunSummary,
} from "@/lib/api";
import { Button, Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { OpsCard, PhaseChip, SectionHead, SectionLabel, TH_FONT as TH, execTone, fmtET, isDeferred } from "./util";

const PAGE = 30; // mirrors the endpoint's default limit

// ── small renderers (shared grammar with the Cycle page's verdict pill) ─────

export function VerifyChip({ v }: { v: string | null | undefined }) {
  if (!v) return <span className="text-[11.5px] text-subtle">—</span>;
  const tone = v === "PASS" ? "ok" : v === "WARN" ? "warn" : v === "FAIL" ? "danger" : "neutral";
  return <Pill tone={tone}>{v}</Pill>;
}

function fmtDur(started: number, finished: number): string {
  const s = Math.round(finished - started);
  if (!Number.isFinite(s) || s < 0) return "—";
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** trigger = "schedule" | "manual:<email>" — manual pulls read loud. */
function TriggerCell({ trigger }: { trigger: string }) {
  if (trigger?.startsWith("manual:")) {
    return (
      <span className="whitespace-nowrap text-[11.5px]">
        <span className="font-semibold text-teal">manual</span>
        <span className="text-subtle"> · {trigger.slice(7)}</span>
      </span>
    );
  }
  return <span className="text-[11.5px] text-subtle">{trigger || "—"}</span>;
}

/** placed / skipped / errors as one mono cell — the scan column. */
function Counts({ r }: { r: AdminRunRow }) {
  const n = (v: number | null, cls: string) => (
    <span className={v ? cls : "text-subtle"}>{v ?? "—"}</span>
  );
  return (
    <span className="font-mono text-[11.5px] tnum">
      {n(r.placed, "font-semibold text-green")}
      <span className="text-subtle"> / </span>
      {n(r.skipped, "text-amber")}
      <span className="text-subtle"> / </span>
      {n(r.errors, "font-semibold text-red")}
    </span>
  );
}

// ── the expanded run summary ────────────────────────────────────────────────

/** Broker-alert skips carry NO note — the reason lives in `alerts`
 *  ([{alertType, …}], the review_equity_order check that blocked the order).
 *  Reduce it to a legible line the same way the executor's own skip-reason
 *  rollup does, so the one row an operator most needs explained isn't blank. */
function alertNote(o: AdminRunOrder): string {
  const a = o.alerts;
  if (!Array.isArray(a) || a.length === 0) return "";
  const parts = a
    .map((x) => typeof x === "string" ? x
      : x && typeof x === "object"
        ? String((x as Record<string, unknown>).alertType
          ?? (x as Record<string, unknown>).message ?? "")
        : "")
    .filter(Boolean);
  return parts.length ? `broker: ${parts.join(" · ")}` : "broker alerts — see raw JSON";
}

/** Flatten members into order-result rows: a member with orders contributes
 *  one row per order; a skipped member contributes their skip line. Same
 *  table grammar as the Session panel's order results. */
function memberRows(members: AdminRunMember[]): {
  name: string; symbol: string; side: string; status: string; note: string;
}[] {
  const rows: { name: string; symbol: string; side: string; status: string; note: string }[] = [];
  for (const m of members) {
    const name = m.name || (m.member_id != null ? `member ${m.member_id}` : "—");
    if (m.orders?.length) {
      for (const o of m.orders) {
        rows.push({
          name,
          symbol: o.symbol || "—",
          side: o.side || "—",
          status: o.status || "—",
          note: o.note || alertNote(o),
        });
      }
    } else {
      rows.push({
        name, symbol: "—", side: "—",
        status: m.skip_reason ? "skipped" : "—",
        note: [m.skip_reason, m.note].filter(Boolean).join(" — "),
      });
    }
  }
  return rows;
}

/** Cents matter for carry and for a clamped commitment — util's `money` rounds
 *  them off, so these figures get their own formatter. */
function usd(n: number): string {
  return n.toLocaleString(undefined, {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/** Commitment write-downs and carry drains are MEMBER-level, so they have no
 *  row in the order table and used to be visible only in the raw JSON. Both are
 *  the member's real money moving: the clamp reduces what the club may deploy
 *  for them from here on, the drain redeploys dollars an earlier cycle missed. */
function moneyNotes(members: AdminRunMember[]): { name: string; kind: "clamp" | "drain"; text: string }[] {
  const out: { name: string; kind: "clamp" | "drain"; text: string }[] = [];
  for (const m of members) {
    const name = m.name || (m.member_id != null ? `member ${m.member_id}` : "—");
    const c = m.commit_clamped;
    if (c && typeof c === "object") {
      // Figures come off the wire: the type is a claim, Number.isFinite is proof.
      const from = Number(c.from);
      const to = Number(c.to);
      if (Number.isFinite(from) && Number.isFinite(to)) {
        out.push({
          name, kind: "clamp",
          text: `committed stake written down ${usd(from)} → ${usd(to)} — the account's deployable capacity`,
        });
      }
    }
    const d = Number(m.carry_drained);
    if (Number.isFinite(d) && d > 0) {
      out.push({
        name, kind: "drain",
        text: `${usd(d)} an earlier cycle could not deploy redeployed into this slice`,
      });
    }
  }
  return out;
}

function RunSummaryBody({ det }: { det: AdminRunDetail }) {
  const s: AdminRunSummary | null = det.summary;
  if (!s) return <div className="text-[12px] text-subtle">no summary recorded for this run</div>;
  const rows = memberRows(s.members ?? []);
  const notes = moneyNotes(s.members ?? []);
  const skips = Object.entries(s.skipped ?? {});
  const v = s.verification;

  return (
    <div className="space-y-3 py-1">
      {/* headline: picks + the run's own caveats */}
      {(s.picks?.length || s.note || s.executed_deferred || s.placed_total != null) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px]">
          {(s.picks ?? []).map((p) => (
            <span key={p} className="rounded-[7px] border border-teal/25 bg-teal/[0.06] px-1.5 py-0.5 font-mono text-[11px]">{p}</span>
          ))}
          {s.placed_total != null && (
            <span className="font-mono text-[11.5px] text-muted tnum">
              session placed total <b className="text-ink">{s.placed_total}</b>
            </span>
          )}
          {s.note && <span className="text-amber">{s.note}</span>}
          {s.executed_deferred && <span className="text-amber">{s.executed_deferred}</span>}
          {s.halted && <Pill tone="danger">HALTED mid-run</Pill>}
        </div>
      )}

      {/* skip-reason breakdown */}
      {skips.length > 0 && (
        <div>
          <SectionLabel>Skip reasons</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {skips.map(([reason, n]) => (
              <Pill key={reason} tone="warn">{reason} <b>{n}</b></Pill>
            ))}
          </div>
        </div>
      )}

      {/* commitment write-downs + carry drains */}
      {notes.length > 0 && (
        <div>
          <SectionLabel>Commitment &amp; carry</SectionLabel>
          <ul className="space-y-0.5 text-[11.5px]">
            {notes.map((n, i) => (
              <li key={i}>
                <span className="text-muted">{n.name}</span>
                <span className="ml-2"><Pill tone={n.kind === "clamp" ? "warn" : "neutral"}>
                  {n.kind === "clamp" ? "clamped" : "carry drained"}
                </Pill></span>
                <span className="ml-2 text-subtle">{n.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* per-member order results */}
      <div>
        <SectionLabel>Member orders</SectionLabel>
        {rows.length === 0 ? (
          <div className="text-[12px] text-subtle">no member rows in this run</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-[12px]">
              <thead>
                <tr>
                  <th className={`${TH} pr-3 text-left`}>member</th>
                  <th className={`${TH} pr-3 text-left`}>symbol</th>
                  <th className={`${TH} pr-3 text-left`}>side</th>
                  <th className={`${TH} pr-3 text-left`}>status</th>
                  <th className={`${TH} text-left`}>note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-hair">
                    <td className="py-1 pr-3 text-muted">{r.name}</td>
                    <td className="py-1 pr-3 font-mono text-[11.5px] font-semibold">{r.symbol}</td>
                    <td className="py-1 pr-3 text-subtle">{r.side}</td>
                    <td className="py-1 pr-3">
                      {/* deferred rides inside the note on skipped rows — same
                          relabel the Session panel does */}
                      <Pill tone={execTone(r.status, r.note)}>
                        {isDeferred(r.note) && r.status === "skipped" ? "deferred" : r.status}
                      </Pill>
                    </td>
                    <td className="py-1 text-[11px] text-subtle">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* post-trade verification */}
      {v && (
        <div>
          <SectionLabel>Verification</SectionLabel>
          <div className="flex flex-wrap items-center gap-2">
            <VerifyChip v={v.verdict} />
            {v.error && <span className="text-[11.5px] text-red">{v.error}</span>}
          </div>
          {(v.members ?? []).filter((m) => m.findings?.length).map((m, i) => (
            <div key={i} className="mt-1.5 text-[11.5px]">
              <span className="text-muted">{m.name || `member ${m.member_id}`}</span>
              <span className="ml-2"><VerifyChip v={m.verdict} /></span>
              <ul className="ml-4 mt-0.5 list-disc space-y-0.5 text-subtle">
                {(m.findings ?? []).map((f, j) => (
                  <li key={j} className={f.level === "fail" ? "text-red" : ""}>
                    {f.code ? <span className="font-mono text-[10.5px]">{f.code}</span> : null} {f.msg}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {v.report && (
            <details className="mt-1.5">
              <summary className="cursor-pointer select-none text-[11.5px] text-teal hover:underline">full report</summary>
              <pre className="mt-1.5 max-h-64 overflow-auto rounded-[10px] border border-hair bg-black/25 p-3 font-mono text-[10.5px] leading-relaxed text-muted">{v.report}</pre>
            </details>
          )}
        </div>
      )}

      {/* everything, verbatim — the forensic floor */}
      <details>
        <summary className="cursor-pointer select-none text-[11.5px] text-teal hover:underline">raw summary JSON</summary>
        <pre className="mt-1.5 max-h-80 overflow-auto rounded-[10px] border border-hair bg-black/25 p-3 font-mono text-[10.5px] leading-relaxed text-muted">{JSON.stringify(s, null, 2)}</pre>
      </details>
    </div>
  );
}

// ── the page ────────────────────────────────────────────────────────────────

type DetailState = { det?: AdminRunDetail; err?: string };

export function RunsPage() {
  const [runs, setRuns] = useState<AdminRunRow[] | null>(null);
  const [missing, setMissing] = useState(false);     // endpoint 404 = older backend
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);           // short page = no more rows
  const [busyMore, setBusyMore] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, DetailState>>({});

  const load = useCallback(async (before?: number) => {
    if (before != null) setBusyMore(true);
    try {
      const r = await api.adminRuns(before);
      setRuns((prev) => (before != null && prev ? [...prev, ...r.runs] : r.runs));
      setDone(r.runs.length < PAGE);
      setErr(null);
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (before != null) setBusyMore(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggle(id: number) {
    const closing = expanded === id;
    setExpanded(closing ? null : id);
    if (closing || details[id]) return;
    setDetails((d) => ({ ...d, [id]: {} }));         // {} = loading
    try {
      const det = await api.adminRun(id);
      setDetails((d) => ({ ...d, [id]: { det } }));
    } catch (e) {
      setDetails((d) => ({
        ...d, [id]: { err: e instanceof Error ? e.message : String(e) },
      }));
    }
  }

  return (
    <OpsCard>
      <SectionHead label="Run reports"
        right={runs?.length
          ? <span className="font-mono text-[11px] tnum text-subtle">{runs.length} loaded</span>
          : undefined} />

      {missing ? (
        <p className="text-[12px] text-subtle">
          run reports unavailable — this backend predates /api/admin/runs.
          Deploy the new backend and every executor invocation (idle runs
          included) will be recorded here.
        </p>
      ) : err && !runs ? (
        <p className="text-[12.5px] text-red">⚠ {err}</p>
      ) : !runs ? (
        <p className="text-[12px] text-subtle">loading run reports…</p>
      ) : runs.length === 0 ? (
        <p className="text-[12px] text-subtle">no runs recorded yet — the next executor invocation writes the first row</p>
      ) : (
        <>
          {err && <p className="mb-2 text-[12px] text-red">⚠ {err} — showing loaded rows</p>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-[12.5px]">
              <thead>
                <tr>
                  <th className={`${TH} pr-3 text-left`}>run</th>
                  <th className={`${TH} pr-3 text-left`}>phase</th>
                  <th className={`${TH} pr-3 text-left`}>mode</th>
                  <th className={`${TH} pr-3 text-left`}>trigger</th>
                  <th className={`${TH} pr-3 text-right`} title="placed / skipped / errors — this run's member orders">p / s / e</th>
                  <th className={`${TH} pr-3 text-left`}>verify</th>
                  <th className={`${TH} text-right`}>took</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const on = expanded === r.id;
                  const d = details[r.id];
                  return [
                    <tr key={r.id}
                      onClick={() => toggle(r.id)}
                      aria-expanded={on}
                      className={`cursor-pointer border-t border-hair transition hover:bg-white/[0.025] ${on ? "bg-white/[0.02]" : ""}`}>
                      {/* one identity cell: the trade date is the run's name,
                          the started clock disambiguates two runs on one date
                          (sell + buy legs, or a re-run) */}
                      <td className="whitespace-nowrap py-1.5 pr-3">
                        <span className="flex items-center gap-1.5">
                          <span className={`inline-block w-2 flex-none text-[10px] text-subtle transition-transform ${on ? "rotate-90" : ""}`} aria-hidden>▸</span>
                          <span className="font-semibold tnum">
                            {r.trade_date ? fmtTradeDate(r.trade_date) : "idle run"}
                          </span>
                        </span>
                        <span className="ml-[14px] block font-mono text-[10.5px] leading-tight text-subtle tnum">
                          {fmtET(r.started_at, true)}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3">
                        {r.phase && r.phase !== "none"
                          ? <PhaseChip phase={r.phase} />
                          : <Pill tone="neutral">idle</Pill>}
                      </td>
                      <td className="py-1.5 pr-3">
                        <span className={`font-mono text-[11.5px] ${r.mode === "live" ? "font-semibold text-teal" : "text-muted"}`}>
                          {r.mode || "—"}
                        </span>
                        {r.mode === "live" && !r.will_place && (
                          <span className="ml-1.5 text-[10.5px] text-subtle" title="live mode, but this run would not place (disarmed / halted / quorum)">no-place</span>
                        )}
                      </td>
                      <td className="max-w-[220px] truncate py-1.5 pr-3"><TriggerCell trigger={r.trigger} /></td>
                      <td className="py-1.5 pr-3 text-right"><Counts r={r} /></td>
                      <td className="py-1.5 pr-3"><VerifyChip v={r.verify_verdict} /></td>
                      <td className="py-1.5 text-right font-mono text-[11px] text-muted tnum">
                        {fmtDur(r.started_at, r.finished_at)}
                      </td>
                    </tr>,
                    on ? (
                      <tr key={`${r.id}-detail`} className="border-t border-hair bg-white/[0.015]">
                        <td colSpan={7} className="px-2 pb-2 sm:px-4">
                          {!d || (!d.det && !d.err) ? (
                            <div className="py-2 text-[12px] text-subtle">loading run summary…</div>
                          ) : d.err ? (
                            <div className="py-2 text-[12px] text-red">⚠ {d.err}</div>
                          ) : (
                            <RunSummaryBody det={d.det!} />
                          )}
                        </td>
                      </tr>
                    ) : null,
                  ];
                })}
              </tbody>
            </table>
          </div>

          {!done && (
            <div className="mt-3 flex justify-center">
              <Button variant="glass" busy={busyMore}
                onClick={() => load(runs[runs.length - 1]?.id)}
                className="!px-3 !py-1.5 !text-[12px]">
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </OpsCard>
  );
}
