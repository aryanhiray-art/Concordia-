"use client";

// The roster: humans first (from panel() — the triage rows with exceptions),
// bots behind a toggle (from /api/admin/bots — ~20 rows of noise when you're
// checking whether Dad's account is connected). Any row opens the drawer for
// balances, orders and the reset-password action.

import { useState } from "react";
import type { AdminBots, AdminMemberRowPlus } from "@/lib/api";
import { Card, Pill, Toggle } from "@/components/ui";
import { AcctTypeBadge, SectionHead, TH_FONT, exceptionTone, fmtAccuracy, fmtET, money, perfLine, stake } from "./util";

const TH = `${TH_FONT} pr-3 text-left`;
const THR = `${TH_FONT} text-right`;

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
    <Card>
      <SectionHead label="Members" right={
        <>
          <span className="text-[12px] text-subtle">
            show {botRows.length > 0 ? botRows.length : ""} bots
          </span>
          <Toggle on={showBots} onChange={setShowBots} />
        </>
      } />

      <div className="mb-3 flex items-center gap-2.5 text-[13.5px]">
        <Pill tone={troubled.length === 0 ? "ok" : "warn"}>
          {healthy.length} of {members.length} ready
        </Pill>
        {troubled.length > 0 && (
          <span className="text-[12.5px] text-muted">
            {troubled.length} need{troubled.length === 1 ? "s" : ""} attention
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-[13px]">
          <thead>
            <tr>
              <th className={TH}>member</th>
              <th className={THR}>stake</th>
              <th className={THR}>power</th>
              <th className={THR}>accuracy</th>
              <th className={`${TH} pl-4`}>acct</th>
              <th className={TH}>opted in</th>
              <th className={THR}>P&amp;L</th>
              <th className={`${TH} pl-4`}>status</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={`h-${m.id}`} {...rowProps(m.id, m.display_name)}>
                <td className="py-2.5 pr-3">
                  <span className="font-display font-semibold">{m.display_name}</span>
                  {m.role !== "member" && <span className="ml-2"><Pill tone="teal">{m.role}</Pill></span>}
                  <div className="text-[11.5px] text-subtle">{m.email}</div>
                </td>
                <td className="py-2.5 text-right text-muted tnum">{stake(m.stake_type, m.stake_value)}</td>
                <td className="py-2.5 text-right tnum">{money(m.voting_power)}</td>
                <td className="py-2.5 text-right text-muted tnum">{fmtAccuracy(m.accuracy)}</td>
                <td className="py-2.5 pl-4"><AcctTypeBadge type={m.acct_type} /></td>
                <td className="py-2.5">{m.opted_in ? "✓" : "—"}</td>
                <td className={`py-2.5 text-right tnum ${m.pnl == null ? "text-subtle" : m.pnl >= 0 ? "text-green" : "text-red"}`}>
                  {m.pnl == null ? "—" : money(m.pnl)}
                </td>
                <td className="py-2.5 pl-4">
                  {m.exceptions?.length ? (
                    <span className="flex flex-wrap gap-1.5">
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
                <td className="py-2.5 pr-3">
                  <span className="font-display font-semibold">{b.display_name}</span>
                  <span className="ml-2"><Pill tone="neutral">bot</Pill></span>
                </td>
                <td className="py-2.5 text-right text-subtle tnum">—</td>
                <td className="py-2.5 text-right tnum">{money(b.voting_power)}</td>
                <td className={`py-2.5 text-right tnum ${b.accuracy == null ? "text-subtle" : b.accuracy >= 0 ? "text-green" : "text-red"}`}>
                  {fmtAccuracy(b.accuracy)}
                </td>
                <td className="py-2.5 pl-4 text-subtle">—</td>
                <td className="py-2.5 text-subtle">—</td>
                <td className="py-2.5 text-right text-subtle tnum">—</td>
                <td className="py-2.5 pl-4 text-[11.5px] text-subtle tnum">
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
        <p className="mt-2 text-[12px] text-subtle">
          bots unavailable{botsErr ? ` — ${botsErr}` : "…"}
        </p>
      )}

      {perfNote && (
        <p className="mt-3 border-t border-hair pt-2.5 text-[12.5px] text-muted tnum">
          club performance (cached): {perfNote}
        </p>
      )}
    </Card>
  );
}
