"use client";

// The last audited actions, newest first — arm flips, kills, resets, verify
// verdicts. Mono + ET timestamps in fixed-width columns: this is the
// console's paper trail and it should read like a log. The bare lines are
// exported so the Overview page can show a 5-line teaser with a "view all →"
// link without duplicating the row markup.

import type { AdminPanel } from "@/lib/types";
import { OpsCard, SectionLabel, fmtET } from "./util";

export function AuditLines({ audit, limit = 20 }: {
  audit: AdminPanel["audit"];
  limit?: number;
}) {
  const rows = (audit ?? []).slice().sort((a, b) => b.ts - a.ts).slice(0, limit);
  if (rows.length === 0) {
    return <div className="text-[12.5px] text-muted">no audit entries</div>;
  }
  return (
    <div className="space-y-[3px] overflow-x-auto">
      {rows.map((a, i) => (
        <div key={i} className="flex gap-3 whitespace-nowrap font-mono text-[11px] leading-normal">
          <span className="w-[158px] flex-none text-subtle tnum">{fmtET(a.ts, true)}</span>
          <span className="w-[118px] flex-none truncate text-teal" title={a.actor}>{a.actor}</span>
          <span className="text-ink">{a.action}</span>
          {a.target && <span className="text-muted">{a.target}</span>}
        </div>
      ))}
    </div>
  );
}

export function AuditTail({ audit }: { audit: AdminPanel["audit"] }) {
  return (
    <OpsCard>
      <SectionLabel>Audit</SectionLabel>
      <AuditLines audit={audit} />
    </OpsCard>
  );
}
