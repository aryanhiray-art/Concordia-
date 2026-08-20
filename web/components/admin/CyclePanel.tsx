"use client";

// Today's phase + the week ahead + the open ballot. The schedule strip comes
// from GET /api/admin/schedule (the executor's own cycle_phase calendar), so
// what the console promises for Thursday is what the 09:31 timer will do —
// and when that endpoint is missing (older backend) the strip degrades to a
// quiet note instead of taking the panel down.

import type { AdminSchedule } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Card, Countdown, Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { PhaseChip, SectionLabel, fmtET } from "./util";

export function CyclePanel({ cycle, schedule, scheduleErr }: {
  cycle: AdminPanel["cycle"];
  schedule: AdminSchedule | null;
  scheduleErr: string | null;
}) {
  return (
    <Card>
      <SectionLabel>Cycle</SectionLabel>

      {/* today + the next 7 trading days */}
      {schedule ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            <span className="font-display font-semibold">today {fmtTradeDate(schedule.today.date)}</span>
            <PhaseChip phase={schedule.today.phase} />
            <span className="text-[12px] text-subtle">cadence {schedule.cadence}</span>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {schedule.upcoming.map((d) => (
              <div key={d.date}
                className="flex min-w-[86px] flex-none flex-col items-center gap-1.5 rounded-[12px] border border-hair bg-white/[0.03] px-3 py-2.5">
                <span className="text-[11px] font-semibold text-muted">{d.weekday}</span>
                <span className="text-[11px] text-subtle tnum">{fmtTradeDate(d.date).slice(0, 5)}</span>
                <PhaseChip phase={d.phase} />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="text-[12.5px] text-subtle">
          schedule unavailable{scheduleErr ? ` — ${scheduleErr}` : "…"}
        </div>
      )}

      {/* open ballot */}
      <div className="mt-4 border-t border-hair pt-3.5">
        <SectionLabel>Open ballot</SectionLabel>
        {cycle.open ? (
          <div className="space-y-2 text-[13.5px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display font-semibold">{fmtTradeDate(cycle.open.trade_date)}</span>
              <span className="text-muted">closes {fmtET(cycle.open.closes_at)}</span>
              <span className="text-[12.5px] text-subtle">
                in <Countdown to={cycle.open.closes_at} />
              </span>
              <Pill tone={cycle.open.quorum_met ? "ok" : "warn"}>
                {cycle.open.quorum_met ? "quorum met" : "quorum not met"}
              </Pill>
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted tnum">
              <span>voters <b className="text-ink">{cycle.open.voters}</b> ({cycle.open.human_voters} human)</span>
              <span>picks so far <b className="text-ink">{cycle.open.picks_so_far}</b></span>
              <span>bots voted <b className="text-ink">{cycle.bots.voted_today}</b>/{cycle.bots.count}</span>
            </div>
          </div>
        ) : (
          <div className="text-[13.5px] text-muted">
            no open session
            <span className="ml-4 text-[13px] tnum">bots voted <b className="text-ink">{cycle.bots.voted_today}</b>/{cycle.bots.count}</span>
          </div>
        )}
      </div>

      {/* the last run that recorded orders */}
      <div className="mt-4 border-t border-hair pt-3.5">
        <SectionLabel>Last run</SectionLabel>
        {cycle.last_executed ? (
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            <span className="font-display font-semibold">{fmtTradeDate(cycle.last_executed.trade_date)}</span>
            <span className="flex flex-wrap gap-x-3 text-[13px] text-muted tnum">
              {Object.entries(cycle.last_executed.by_status).map(([k, v]) => (
                <span key={k} className={k === "error" && v > 0 ? "font-semibold text-red" : ""}>
                  {k} <b className={k === "error" && v > 0 ? "text-red" : "text-ink"}>{v}</b>
                </span>
              ))}
            </span>
            {cycle.last_executed.latest_verify_verdict && (
              <span className="ml-auto">
                <Pill tone={
                  cycle.last_executed.latest_verify_verdict === "PASS" ? "ok"
                    : cycle.last_executed.latest_verify_verdict === "WARN" ? "warn"
                    : cycle.last_executed.latest_verify_verdict === "FAIL" ? "danger" : "neutral"
                }>
                  verify {cycle.last_executed.latest_verify_verdict}
                </Pill>
              </span>
            )}
          </div>
        ) : (
          <div className="text-[13.5px] text-muted">no executed run yet</div>
        )}
      </div>
    </Card>
  );
}
