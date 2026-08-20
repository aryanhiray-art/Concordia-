"use client";

// The latest session end-to-end: the ballot's tally, the consensus basket,
// and — the part the owner actually watches on both-day runs — every member's
// order results, with DEFERRED counted out loud. Deferrals aren't a status of
// their own (the executor records them as skipped + "deferred — beyond
// settled buying power"), so this panel is where they become visible.

import type { AdminOverview } from "@/lib/api";
import { Card, Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { SectionLabel, execTone, fmtET, isDeferred, money } from "./util";

export function SessionPanel({ overview, err }: {
  overview: AdminOverview | null;
  err: string | null;
}) {
  if (!overview) {
    return (
      <Card>
        <SectionLabel>Session</SectionLabel>
        <div className="text-[12.5px] text-subtle">
          session view unavailable{err ? ` — ${err}` : "…"}
        </div>
      </Card>
    );
  }

  const s = overview.session;
  const execs = overview.executions ?? [];
  const deferred = execs.filter((e) => isDeferred(e.error)).length;
  const byStatus: Record<string, number> = {};
  for (const e of execs) {
    // deferred rows counted under their own headline key, not "skipped" —
    // that split is exactly what this panel exists to show
    const k = isDeferred(e.error) ? "deferred" : e.status;
    byStatus[k] = (byStatus[k] || 0) + 1;
  }
  const tally = (s?.tally ?? []).filter((t) => t.votes > 0).slice(0, 12);

  return (
    <Card>
      <SectionLabel>Session</SectionLabel>
      {!s ? (
        <div className="text-[13.5px] text-muted">no session yet</div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            <span className="font-display font-semibold">{fmtTradeDate(s.trade_date)}</span>
            <Pill tone={s.status === "open" ? "ok" : s.status === "executed" ? "teal" : "neutral"}>{s.status}</Pill>
            <span className="text-[12.5px] text-muted tnum">
              {s.voters} voter{s.voters === 1 ? "" : "s"} · closes {fmtET(s.closes_at)}
            </span>
          </div>

          {s.picks?.length > 0 && (
            <div>
              <SectionLabel>Consensus basket</SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {s.picks.map((p) => (
                  <span key={p.symbol}
                    className="rounded-[10px] border border-teal/30 bg-teal/[0.08] px-2.5 py-1 font-mono text-[12px]">
                    {p.symbol}<span className="ml-1.5 text-teal tnum">{p.votes}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {tally.length > 0 && (
            <div>
              <SectionLabel>Tally (top {tally.length})</SectionLabel>
              <div className="flex flex-wrap gap-x-3.5 gap-y-1 font-mono text-[11.5px] text-muted">
                {tally.map((t) => (
                  <span key={t.symbol}>
                    {t.symbol}<span className="text-teal">·{t.votes}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="border-t border-hair pt-3.5">
            <SectionLabel>Order results</SectionLabel>
            {execs.length === 0 ? (
              <div className="text-[13px] text-muted">no orders recorded for this session</div>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  {deferred > 0 && (
                    <Pill tone="warn"><b>{deferred}</b> deferred</Pill>
                  )}
                  {Object.entries(byStatus)
                    .filter(([k]) => k !== "deferred")
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => (
                      <Pill key={k} tone={execTone(k)}>{k} <b>{v}</b></Pill>
                    ))}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-[12.5px]">
                    <thead>
                      <tr>
                        <th className="label pb-2 pr-3 text-left font-semibold">member</th>
                        <th className="label pb-2 pr-3 text-left font-semibold">symbol</th>
                        <th className="label pb-2 pr-3 text-left font-semibold">side</th>
                        <th className="label pb-2 text-right font-semibold">amount</th>
                        <th className="label pb-2 pl-4 text-left font-semibold">status</th>
                        <th className="label pb-2 pl-4 text-left font-semibold">note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {execs.map((e, i) => (
                        <tr key={i} className="border-t border-hair">
                          <td className="py-2 pr-3 text-muted">{e.display_name || "—"}</td>
                          <td className="py-2 pr-3 font-mono font-semibold">{e.symbol}</td>
                          <td className="py-2 pr-3 text-subtle">{e.side || "—"}</td>
                          <td className="py-2 text-right text-muted tnum">
                            {e.dollar_amount != null ? money(e.dollar_amount) : "—"}
                          </td>
                          <td className="py-2 pl-4">
                            <Pill tone={execTone(e.status, e.error)}>
                              {isDeferred(e.error) ? "deferred" : e.status}
                            </Pill>
                          </td>
                          <td className="py-2 pl-4 text-[11.5px] text-subtle">{e.error || ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
