"use client";

// The at-a-glance page — the control-room summary the owner scans before
// coffee. NO heavy tables here: the StatusBar verdict is the hero, four stat
// tiles link into the deep pages, then a compact cycle strip + the open
// ballot two-up, then the last five audit lines. Every tile renders sensibly
// when its enrichment endpoint is missing (dim note, never blank).

import { useState } from "react";
import { api, type AdminBots, type AdminPhase, type AdminSchedule } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Button, Countdown, Pill } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { StatusBar } from "./StatusBar";
import { OpenBallotBody, ScheduleStrip } from "./CyclePanel";
import { AuditLines } from "./AuditTail";
import { AdminLink, MoreLink, OpsCard, PhaseChip, SectionHead } from "./util";

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
      className="glass glass-2 block rounded-[13px] px-3 py-2 transition hover:border-teal/40 hover:bg-white/[0.04]">
      <div className="flex items-center justify-between gap-2">
        <span className="ops-label">{label}</span>
        <span className="text-[11px] text-subtle" aria-hidden>→</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <span className={`font-display text-[18px] font-bold leading-none tnum ${
          tone === "ok" ? "text-green" : tone === "warn" ? "text-amber" : "text-ink"
        }`}>
          {value}
        </span>
        {chip}
      </div>
      {sub && <div className="mt-1 text-[11.5px] leading-tight text-subtle">{sub}</div>}
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

export function OverviewPage({ panel, schedule, scheduleErr, bots, botsErr, cadence, inviteCode, onChanged }: {
  panel: AdminPanel;
  schedule: AdminSchedule | null;
  scheduleErr: string | null;
  bots: AdminBots | null;
  botsErr: string | null;
  cadence: string | null;
  inviteCode?: string | null;
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
      <StatusBar state={panel.state} cadence={cadence} inviteCode={inviteCode} onChanged={onChanged} />

      {/* corporate-action drift: a ledger phantom paired with an unledgered
          live position = probable ticker rename (the AVB→VMRK class of bug).
          One click repairs the ledger; until then the position is unsellable. */}
      {(panel.drift ?? []).map((d) => (
        <DriftAlert key={`${d.old}-${d.new}`} d={d} onChanged={onChanged} />
      ))}

      {/* stat tiles — each is a doorway into its page (2-up even on phones:
          they're small, and a 375px column of four full-width tiles is all
          scroll) */}
      <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4 xl:gap-3">
        <StatTile
          href="/members"
          label="Members ready"
          value={`${ready}/${members.length}`}
          tone={troubled === 0 ? "ok" : "warn"}
          sub={troubled > 0
            ? <span className="text-amber">{troubled} need{troubled === 1 ? "s" : ""} attention</span>
            : undefined}
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
      <div className="grid items-stretch gap-3 lg:grid-cols-2">
        <OpsCard>
          <SectionHead label="Cycle — next 3 days"
            right={<MoreLink href="/cycle">full calendar</MoreLink>} />
          <ScheduleStrip schedule={schedule} scheduleErr={scheduleErr} days={3} />
        </OpsCard>
        <OpsCard>
          <SectionHead label="Open ballot"
            right={<MoreLink href="/cycle">session detail</MoreLink>} />
          <OpenBallotBody cycle={panel.cycle} />
        </OpsCard>
      </div>

      {/* the last five audited actions */}
      <OpsCard>
        <SectionHead label="Audit — last 5"
          right={<MoreLink href="/audit">view all</MoreLink>} />
        <AuditLines audit={panel.audit} limit={5} />
      </OpsCard>
    </>
  );
}

// ── ticker-rename repair banner ─────────────────────────────────────────────
function DriftAlert({ d, onChanged }: {
  d: { old: string; new: string; members: string[]; live_value: number };
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function migrate() {
    if (!window.confirm(
      `Migrate the club ledger ${d.old} → ${d.new}?\n\n` +
      `Renames the execution history, trues share counts to each account, and makes ` +
      `${d.new} votable. The position becomes sellable again at the next rebalance.`
    )) return;
    setBusy(true); setErr(null);
    try {
      await api.adminRenameSymbol(d.old, d.new);
      onChanged();
    } catch (e: any) {
      setErr(e?.message || "migration failed");
    } finally { setBusy(false); }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[14px] border border-amber/30 bg-amber/[0.07] px-4 py-2.5 text-[12.5px] text-amber">
      <span>
        ticker change detected: <b className="font-mono">{d.old}</b> → <b className="font-mono">{d.new}</b>
        {" "}· {d.members.join(", ")} · ${d.live_value.toFixed(2)} unsellable until migrated
      </span>
      <Button variant="glass" busy={busy} onClick={migrate}
        className="!ml-auto !px-3 !py-1.5 !text-[12px]">
        Migrate ledger
      </Button>
      {err && <span className="text-red">⚠ {err}</span>}
    </div>
  );
}
