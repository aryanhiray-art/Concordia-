"use client";

// The last audited actions, newest first — arm flips, kills, resets, verify
// verdicts. Mono + ET timestamps: this is the console's paper trail. The
// bare lines are exported so the Overview page can show a 5-line teaser with
// a "view all →" link without duplicating the row markup.

import type { AdminPanel } from "@/lib/types";
import { Card } from "@/components/ui";
import { SectionLabel, fmtET } from "./util";

export function AuditLines({ audit, limit = 20 }: {
  audit: AdminPanel["audit"];
  limit?: number;
}) {
  const rows = (audit ?? []).slice().sort((a, b) => b.ts - a.ts).slice(0, limit);
  if (rows.length === 0) {
    return <div className="text-[13px] text-muted">no audit entries</div>;
  }
  return (
    <div className="space-y-1 overflow-x-auto">
      {rows.map((a, i) => (
        <div key={i} className="flex gap-3 whitespace-nowrap font-mono text-[11.5px] leading-relaxed">
          <span className="text-subtle tnum">{fmtET(a.ts, true)}</span>
          <span className="text-teal">{a.actor}</span>
          <span className="text-ink">{a.action}</span>
          {a.target && <span className="text-muted">{a.target}</span>}
        </div>
      ))}
    </div>
  );
}

export function AuditTail({ audit }: { audit: AdminPanel["audit"] }) {
  return (
    <Card>
      <SectionLabel>Audit</SectionLabel>
      <AuditLines audit={audit} />
    </Card>
  );
}
