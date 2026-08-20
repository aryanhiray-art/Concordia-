"use client";

// The last 20 audited actions, newest first — arm flips, kills, resets,
// verify verdicts. Mono + ET timestamps: this is the page's paper trail.

import type { AdminPanel } from "@/lib/types";
import { Card } from "@/components/ui";
import { SectionLabel, fmtET } from "./util";

export function AuditTail({ audit }: { audit: AdminPanel["audit"] }) {
  const rows = (audit ?? []).slice().sort((a, b) => b.ts - a.ts).slice(0, 20);
  return (
    <Card>
      <SectionLabel>Audit</SectionLabel>
      {rows.length === 0 ? (
        <div className="text-[13px] text-muted">no audit entries</div>
      ) : (
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
      )}
    </Card>
  );
}
