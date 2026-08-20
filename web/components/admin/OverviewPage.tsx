"use client";

// The at-a-glance page — the control-room summary the owner scans before
// coffee. NO heavy tables here: the StatusBar verdict is the hero, four stat
// tiles link into the deep pages, then a compact cycle strip + the open
// ballot two-up, then the last five audit lines. Every tile renders sensibly
// when its enrichment endpoint is missing (dim note, never blank).

import type { AdminBots, AdminPhase, AdminSchedule } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Card, Countdown, Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { StatusBar } from "./StatusBar";
import { OpenBallotBody, ScheduleStrip } from "./CyclePanel";
import { AuditLines } from "./AuditTail";
import { AdminLink, MoreLink, PhaseChip, SectionHead } from "./util";

// ── stat tile: one number, one link, one destination ────────────────────────

function StatTile({ href, label, value, chip, sub, tone }: {
  href: string;
  label: string;
  value: React.ReactNode;
  chip?: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "ok" | "warn";
}) {
  return (
    <AdminLink href={href}
      className="glass glass-2 block rounded-[14px] px-4 py-3.5 transition hover:border-teal/40 hover:bg-white/[0.04]">
      <div className="flex items-center justify-between gap-2">
        <span className="label">{label}</span>
        <span className="text-[12px] text-subtle" aria-hidden>→</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className={`font-display text-[21px] font-bold leading-none tnum ${
          tone === "ok" ? "text-green" : tone === "warn" ? "text-amber" : "text-ink"
        }`}>
          {value}
        </span>
        {chip}
      </div>
      {sub && <div className="mt-1.5 text-[12px] text-subtle">{sub}</div>}
    </AdminLink>
  );
}

/** The next trading day the executor will act on: today if today has a phase,
 *  else the first non-idle upcoming day. Null = schedule endpoint missing. */
function nextRun(schedule: AdminSchedule | null):
  { date: string; phase: AdminPhase; when: string } | null {
  if (!schedule) return null;
  if (schedule.today.phase !== "idle") return { ...schedule.today, when: "today" };
  const d = schedule.upcoming.find((u) => u.phase !== "idle");
  return d ? { date: d.date, phase: d.phase, when: d.weekday } : null;
}

// ── the page ────────────────────────────────────────────────────────────────

export function OverviewPage({ panel, schedule, scheduleErr, bots, botsErr, cadence, onChanged }: {
  panel: AdminPanel;
  schedule: AdminSchedule | null;
  scheduleErr: string | null;
  bots: AdminBots | null;
  botsErr: string | null;
  cadence: string | null;
  onChanged: () => void;
}) {
  const members = panel.members;
  const ready = members.filter((m) => !m.exceptions?.length).length;
  const troubled = members.length - ready;
  const open = panel.cycle.open;
  const run = nextRun(schedule);
  const botCount = bots?.bots.length ?? panel.cycle.bots.count;

  return (
    <>
      {/* the verdict + every lever — the reason this host exists */}
      <StatusBar state={panel.state} cadence={cadence} onChanged={onChanged} />

      {/* stat tiles — each is a doorway into its page */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          href="/members"
          label="Members ready"
          value={`${ready}/${members.length}`}
          tone={troubled === 0 ? "ok" : "warn"}
          sub={troubled > 0
            ? <span className="text-amber">{troubled} need{troubled === 1 ? "s" : ""} attention</span>
            : "all accounts ready"}
        />
        <StatTile
          href="/cycle"
          label="Open ballot"
          value={open ? open.voters : "—"}
          chip={open
            ? <Pill tone={open.quorum_met ? "ok" : "warn"}>{open.quorum_met ? "quorum" : "no quorum"}</Pill>
            : undefined}
          sub={open
            ? <span className="tnum">voters · closes in <Countdown to={open.closes_at} /></span>
            : "no open session"}
        />
        <StatTile
          href="/cycle"
          label="Next run"
          value={run ? fmtTradeDate(run.date).slice(0, 5) : "—"}
          chip={run ? <PhaseChip phase={run.phase} /> : undefined}
          sub={run
            ? run.when
            : schedule
              ? "no trading day scheduled"
              : `schedule unavailable${scheduleErr ? ` — ${scheduleErr}` : "…"}`}
        />
        <StatTile
          href="/bots"
          label="Bots"
          value={botCount}
          chip={bots
            ? <Pill tone={bots.llm.reachable ? "ok" : "danger"}>{bots.llm.reachable ? "LLM ok" : "LLM down"}</Pill>
            : undefined}
          sub={bots
            ? <span className="tnum">{panel.cycle.bots.voted_today}/{panel.cycle.bots.count} voted today</span>
            : `bots unavailable${botsErr ? ` — ${botsErr}` : "…"}`}
        />
      </div>

      {/* compact cycle strip | the open ballot */}
      <div className="grid items-stretch gap-4 lg:grid-cols-2">
        <Card>
          <SectionHead label="Cycle — next 3 days"
            right={<MoreLink href="/cycle">full calendar</MoreLink>} />
          <ScheduleStrip schedule={schedule} scheduleErr={scheduleErr} days={3} />
        </Card>
        <Card>
          <SectionHead label="Open ballot"
            right={<MoreLink href="/cycle">session detail</MoreLink>} />
          <OpenBallotBody cycle={panel.cycle} />
        </Card>
      </div>

      {/* the last five audited actions */}
      <Card>
        <SectionHead label="Audit — last 5"
          right={<MoreLink href="/audit">view all</MoreLink>} />
        <AuditLines audit={panel.audit} limit={5} />
      </Card>
    </>
  );
}
