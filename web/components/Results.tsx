"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ExecutionRow, SessionView } from "@/lib/types";
import { AllocRow, Card, Pill } from "./ui";

const STATUS_TONE: Record<string, "ok" | "teal" | "warn" | "danger" | "neutral"> = {
  placed: "ok", reviewed: "teal", planned: "neutral", skipped: "warn", error: "danger",
};

export function Results({ session }: { session: SessionView }) {
  const [rows, setRows] = useState<ExecutionRow[]>([]);
  useEffect(() => { api.results().then((r) => setRows(r.executions || [])).catch(() => {}); },
    [session.id, session.status]);

  const maxVotes = Math.max(1, ...session.picks.map((p) => p.votes));

  return (
    <Card className="rise">
      <div className="flex items-center justify-between">
        <h2 className="label">Consensus basket · {session.trade_date}</h2>
        <Pill tone={session.status === "executed" ? "ok" : "teal"}>{session.status}</Pill>
      </div>

      {session.picks.length === 0 ? (
        <p className="mt-4 text-[13px] text-subtle">No consensus picks for this session.</p>
      ) : (
        <div className="mt-4 rounded-[14px] border border-teal/20 bg-teal/[0.05] p-4">
          <div className="label !text-teal mb-3">{session.picks.length}-name basket · {session.voters} voters</div>
          <div className="flex flex-col gap-2.5">
            {session.picks.map((p) => (
              <AllocRow key={p.symbol} ticker={p.symbol} votes={p.votes} max={maxVotes} />
            ))}
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div className="mt-5">
          <div className="label mb-2.5">Order status</div>
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead className="label !text-[10px] text-left">
                <tr className="text-subtle">
                  {rows[0].display_name && <th className="pb-2 pr-4 font-semibold">Member</th>}
                  <th className="pb-2 pr-4 font-semibold">Symbol</th>
                  <th className="pb-2 pr-4 font-semibold">Amount</th>
                  <th className="pb-2 pr-4 font-semibold">Status</th>
                  <th className="pb-2 font-semibold">Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-white/[0.07]">
                    {r.display_name && <td className="py-2 pr-4 text-muted">{r.display_name}</td>}
                    <td className="py-2 pr-4 font-display font-semibold">{r.symbol}</td>
                    <td className="py-2 pr-4 tnum">{r.dollar_amount != null ? `$${r.dollar_amount.toFixed(2)}` : "—"}</td>
                    <td className="py-2 pr-4"><Pill tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Pill></td>
                    <td className="py-2 text-[11.5px] text-subtle">{r.error || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}
