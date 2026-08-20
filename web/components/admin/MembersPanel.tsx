"use client";

// The roster: humans first (from panel() — the triage rows with exceptions),
// bots behind a toggle (from /api/admin/bots — ~20 rows of noise when you're
// checking whether Dad's account is connected). Any row opens the drawer for
// balances, orders and the reset-password action.

import { useState } from "react";
import type { AdminBots, AdminMemberRowPlus } from "@/lib/api";
import { Pill, Toggle } from "@/components/ui";
import { AcctTypeBadge, OpsCard, SectionHead, TH_FONT, exceptionTone, fmtAccuracy, fmtET, money, perfLine, stake } from "./util";

const TH = `${TH_FONT} pr-3 text-left`;
const THR = `${TH_FONT} pl-3 text-right`;
// numeric body cell: mono + tabular, right-aligned — columns of figures line up
const NUM = "py-2 pl-3 text-right font-mono text-[12px] tnum";

export function MembersPanel({ members, bots, botsErr, perf, onOpen }: {
  members: AdminMemberRowPlus[];
  bots: AdminBots | null;
  botsErr: string | null;
  perf: unknown;
  onOpen: (id: number) => void;
}) {
  const [showBots, setShowBots] = useState(false);
  const healthy = members.filter((m) => !m.exceptions?.length);
  const troubled = members.filter((m) => m.exceptions?.length);
  const botRows = bots?.bots ?? [];
  const perfNote = perfLine(perf);

  // shared row affordance: click or Enter/Space opens the drawer
  const rowProps = (id: number, name: string) => ({
    onClick: () => onOpen(id),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(id); }
    },
    tabIndex: 0,
    role: "button" as const,
    "aria-label": `Open ${name} detail`,
    className: "cursor-pointer border-t border-hair transition hover:bg-white/[0.03]",
  });

  return (
    <OpsCard>
      <SectionHead label="Members" right={
        <>
          <Pill tone={troubled.length === 0 ? "ok" : "warn"}>
            {healthy.length}/{members.length} ready
          </Pill>
          <span className="ml-1 text-[11.5px] text-subtle">
            bots{botRows.length > 0 ? ` (${botRows.length})` : ""}
          </span>
          <Toggle on={showBots} onChange={setShowBots} />
        </>
      } />

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-[12.5px]">
          <thead>
            <tr>
              <th className={TH}>member</th>
              <th className={THR}>stake</th>
              <th className={THR}>power</th>
              <th className={THR}>accuracy</th>
              <th className={`${TH} pl-4`}>acct</th>
              <th className={TH}>opt</th>
              {/* "open": unrealized on CURRENT positions only — broker cost
                  basis resets every rebalance, so this is not a lifetime
                  return (the owner read it as one; see 08/20 exchange) */}
              <th className={THR}>open P&amp;L</th>
              <th className={`${TH} pl-4`}>status</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={`h-${m.id}`} {...rowProps(m.id, m.display_name)}>
                <td className="py-2 pr-3">
                  <span className="font-display text-[13px] font-semibold leading-tight">{m.display_name}</span>
                  {m.role !== "member" && <span className="ml-2"><Pill tone="teal">{m.role}</Pill></span>}
                  <div className="max-w-[200px] truncate text-[11px] leading-tight text-subtle" title={m.email}>{m.email}</div>
                </td>
                <td className={`${NUM} text-muted`}>{stake(m.stake_type, m.stake_value)}</td>
                <td className={NUM}>{money(m.voting_power)}</td>
                <td className={`${NUM} text-muted`}>{fmtAccuracy(m.accuracy)}</td>
                <td className="py-2 pl-4"><AcctTypeBadge type={m.acct_type} /></td>
                <td className="py-2">{m.opted_in ? "✓" : "—"}</td>
                <td className={`${NUM} ${m.pnl == null ? "text-subtle" : m.pnl >= 0 ? "text-green" : "text-red"}`}>
                  {m.pnl == null ? "—" : (
                    <>
                      {m.pnl >= 0 ? "+" : ""}{money(m.pnl)}
                      {m.pnl_pct != null && (
                        <span className="ml-1 text-[10.5px] opacity-75">{m.pnl_pct >= 0 ? "+" : ""}{m.pnl_pct.toFixed(1)}%</span>
                      )}
                    </>
                  )}
                </td>
                <td className="py-2 pl-4">
                  {m.exceptions?.length ? (
                    <span className="flex flex-wrap gap-1">
                      {m.exceptions.map((e, i) => (
                        <Pill key={i} tone={exceptionTone(e)}>{e}</Pill>
                      ))}
                    </span>
                  ) : (
                    <Pill tone="ok">ready</Pill>
                  )}
                </td>
              </tr>
            ))}

            {showBots && botRows.map((b) => (
              <tr key={`b-${b.id}`} {...rowProps(b.id, b.display_name)}>
                <td className="py-2 pr-3">
                  <span className="font-display text-[13px] font-semibold leading-tight">{b.display_name}</span>
                  <span className="ml-2"><Pill tone="neutral">bot</Pill></span>
                </td>
                <td className={`${NUM} text-subtle`}>—</td>
                <td className={NUM}>{money(b.voting_power)}</td>
                <td className={`${NUM} ${b.accuracy == null ? "text-subtle" : b.accuracy >= 0 ? "text-green" : "text-red"}`}>
                  {fmtAccuracy(b.accuracy)}
                </td>
                <td className="py-2 pl-4 text-subtle">—</td>
                <td className="py-2 text-subtle">—</td>
                <td className={`${NUM} text-subtle`}>—</td>
                <td className="py-2 pl-4 font-mono text-[11px] text-subtle tnum">
                  {b.last_vote_at ? `voted ${fmtET(b.last_vote_at, true)}` : "never voted"}
                </td>
              </tr>
            ))}

            {members.length === 0 && (
              <tr><td colSpan={8} className="py-2 text-muted">no members</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {showBots && !bots && (
        <p className="mt-2 text-[11.5px] text-subtle">
          bots unavailable{botsErr ? ` — ${botsErr}` : "…"}
        </p>
      )}

      {perfNote && (
        <p className="mt-2.5 border-t border-hair pt-2 font-mono text-[11.5px] text-muted tnum">
          perf (cached): {perfNote}
        </p>
      )}
    </OpsCard>
  );
}
