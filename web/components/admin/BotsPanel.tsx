"use client";

// The bot bench: per-bot voting power, scored accuracy and last picks, plus
// the LLM health chip — the single fact that decides whether tomorrow's 20
// bot ballots happen at all. Cards click through to the member drawer (the
// thesis lives there).

import type { AdminBots } from "@/lib/api";
import { Pill } from "@/components/ui";
import { OpsCard, SectionHead, fmtAccuracy, fmtET, money } from "./util";

const MAX_PICK_CHIPS = 6;

export function BotsPanel({ bots, err, onOpen }: {
  bots: AdminBots | null;
  err: string | null;
  onOpen: (id: number) => void;
}) {
  return (
    <OpsCard>
      <SectionHead label="Bots" right={bots && (
        <Pill tone={bots.llm.reachable ? "ok" : "danger"}>
          {bots.llm.reachable ? "LLM ok" : "LLM down"}
        </Pill>
      )} />

      {!bots ? (
        <div className="text-[12px] text-subtle">
          bots unavailable{err ? ` — ${err}` : "…"}
        </div>
      ) : (
        <>
          {!bots.llm.reachable && bots.llm.detail && (
            <p className="mb-2 text-[11.5px] text-red">⚠ {bots.llm.detail}</p>
          )}
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {bots.bots.map((b) => (
              <button key={b.id} type="button" onClick={() => onOpen(b.id)}
                className="rounded-[11px] border border-hair bg-white/[0.03] p-2.5 text-left transition hover:border-white/20 hover:bg-white/[0.05]">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-display text-[12.5px] font-semibold">{b.display_name}</span>
                  <span className="flex-none font-mono text-[12px] tnum">{money(b.voting_power)}</span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 font-mono text-[11px] text-subtle tnum">
                  <span className={b.accuracy == null ? "" : b.accuracy >= 0 ? "text-green" : "text-red"}>
                    {fmtAccuracy(b.accuracy)}
                  </span>
                  <span>· {b.last_vote_at ? `voted ${fmtET(b.last_vote_at, true)}` : "never voted"}</span>
                </div>
                {b.last_picks.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {b.last_picks.slice(0, MAX_PICK_CHIPS).map((p) => (
                      <span key={p} className="rounded-[6px] border border-hair bg-white/[0.04] px-1.5 py-px font-mono text-[10.5px] text-muted">
                        {p}
                      </span>
                    ))}
                    {b.last_picks.length > MAX_PICK_CHIPS && (
                      <span className="px-1 py-px text-[10.5px] text-subtle">
                        +{b.last_picks.length - MAX_PICK_CHIPS}
                      </span>
                    )}
                  </div>
                )}
              </button>
            ))}
            {bots.bots.length === 0 && (
              <div className="text-[12.5px] text-muted">no bots seeded</div>
            )}
          </div>
        </>
      )}

      <p className="mt-3 border-t border-hair pt-2 text-[11px] text-subtle">
        inference via SSH tunnel to Forge — this box can&apos;t host the models
      </p>
    </OpsCard>
  );
}
