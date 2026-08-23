"use client";

// /metrics — the tracking layer's console page. KPI tiles for the numbers the
// owner steers by (members, activation, participation, committed capital),
// then three time-series charts over the nightly daily_metrics rows, then the
// week-over-week voting-retention cohort grid. Self-fetching (RunsPage
// precedent): metrics are a separate, cheaper endpoint than /panel and don't
// need the console's 60s poll — a manual Refresh re-mounts nothing, so this
// panel refetches on its own mount and exposes its own reload.
//
// Charts are hand-rolled inline SVG (ClubPerformance precedent — the repo has
// no chart library and this deliberately doesn't add one): lines scale in the
// SVG, all text lives in HTML.
//
// Degrades: a backend that predates /api/admin/metrics 404s → one dim note; an
// empty series (nightly job hasn't fired yet) says so instead of blanking.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AdminMetrics, type AdminMetricsDay } from "@/lib/api";
import { Button } from "@/components/ui";
import { OpsCard, SectionHead, SectionLabel, TH_FONT as TH, money } from "./util";

// ── formatting ──────────────────────────────────────────────────────────────

const pct = (v: number | null | undefined, digits = 0): string =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

const fmtDay = (d: string): string => {
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, dd)).toLocaleDateString("en-US",
    { month: "short", day: "numeric", timeZone: "UTC" });
};

// ── tiny line chart (ClubPerformance's smoothing, panel-sized) ──────────────

const W = 640, H = 150, PAD = 8;

function smooth(pts: number[][]): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)}`
      + ` ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)}`
      + ` ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

type Series = { label: string; color: string; width?: number; values: (number | null)[] };

/** One chart, 1–2 lines over shared day labels. Null points (e.g. no-ballot
 *  days) are simply skipped — segments bridge across them. */
function MetricChart({ days, series, fmt }: {
  days: string[];
  series: Series[];
  fmt: (v: number) => string;
}) {
  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null);
  if (!days.length || !all.length) {
    return <div className="text-[12px] text-subtle">No data yet.</div>;
  }
  // zero-anchored: growth/participation/capital charts must not amplify noise
  let lo = Math.min(0, ...all);
  let hi = Math.max(...all);
  if (hi === lo) hi = lo + 1;
  const breathe = (hi - lo) * 0.08;
  lo -= breathe; hi += breathe;
  const x = (i: number) => PAD + (days.length <= 1 ? 0.5 : i / (days.length - 1)) * (W - 2 * PAD);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (H - 2 * PAD);
  const grid = [0.25, 0.5, 0.75].map((f) => lo + f * (hi - lo));
  const last = (s: Series) => {
    for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i] != null) return s.values[i] as number;
    return null;
  };
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] font-semibold tnum">
        {series.map((s) => {
          const v = last(s);
          return (
            <span key={s.label} className="flex items-center gap-1.5">
              <span className="inline-block h-[3px] w-3.5 rounded-full" style={{ background: s.color }} />
              <span className="text-muted">{s.label}</span>
              <span className="text-ink">{v == null ? "—" : fmt(v)}</span>
            </span>
          );
        })}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-1.5 block w-full" role="img"
        aria-label={series.map((s) => s.label).join(" and ")}>
        {grid.map((g) => (
          <line key={g} x1={PAD} x2={W - PAD} y1={y(g)} y2={y(g)} stroke="rgba(255,255,255,0.06)" />
        ))}
        {series.map((s) => {
          const pts = s.values
            .map((v, i) => (v == null ? null : [x(i), y(v)]))
            .filter((p): p is number[] => p != null);
          if (pts.length === 1) {
            return <circle key={s.label} cx={pts[0][0]} cy={pts[0][1]} r="3" fill={s.color} />;
          }
          return (
            <path key={s.label} d={smooth(pts)} fill="none" stroke={s.color}
              strokeWidth={s.width ?? 2} strokeLinecap="round" />
          );
        })}
      </svg>
      <div className="mt-0.5 flex justify-between text-[10.5px] text-subtle tnum">
        <span>{fmtDay(days[0])}</span>
        {days.length > 1 && <span>{fmtDay(days[days.length - 1])}</span>}
      </div>
    </div>
  );
}

// ── KPI tile (StatTile's look without the link — these aren't doorways) ─────

function Kpi({ label, value, sub }: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="glass glass-2 rounded-[13px] px-3 py-2">
      <span className="ops-label">{label}</span>
      <div className="mt-1.5 font-display text-[18px] font-bold leading-none text-ink tnum">{value}</div>
      {sub && <div className="mt-1 text-[11.5px] leading-tight text-subtle">{sub}</div>}
    </div>
  );
}

// ── the page ────────────────────────────────────────────────────────────────

export function MetricsPanel() {
  const [data, setData] = useState<AdminMetrics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [deferred, setDeferred] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.adminMetrics(180));
      setErr(null);
      setDeferred(false);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) setDeferred(true);
      else setErr(e instanceof Error ? e.message : "failed to load metrics");
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (deferred) {
    return (
      <OpsCard>
        <SectionLabel>Metrics</SectionLabel>
        <p className="text-[12.5px] text-subtle">
          The backend predates /api/admin/metrics — deploy the API to light this page up.
        </p>
      </OpsCard>
    );
  }
  if (err) {
    return (
      <OpsCard>
        <SectionLabel>Metrics</SectionLabel>
        <p className="text-[12.5px] text-red">⚠ {err}</p>
        <div className="mt-3"><Button variant="glass" busy={busy} onClick={load}>Retry</Button></div>
      </OpsCard>
    );
  }
  if (!data) {
    return <div className="py-16 text-center text-[13px] text-subtle">loading metrics…</div>;
  }

  const s = data.series;
  const now: AdminMetricsDay = data.snapshot;
  const days = s.map((d) => d.date);

  // 7-day participation: mean over the trailing week's ballot days (snapshot's
  // own day included via the live row when the series already has today).
  const ballots = s.slice(-7).map((d) => d.ballot_participation)
    .filter((v): v is number => v != null);
  const part7 = ballots.length ? ballots.reduce((a, b) => a + b, 0) / ballots.length : null;
  const activationPct = now.member_count ? now.activation_count / now.member_count : null;

  return (
    <div className="space-y-3">
      {/* KPI tiles — the live snapshot, not last night's row */}
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Kpi label="Members" value={now.member_count}
          sub={`${now.dau} active today · ${now.wau} this week`} />
        <Kpi label="Activated" value={pct(activationPct)}
          sub={`${now.activation_count} connected + opted in + staked`} />
        <Kpi label="7-day participation" value={pct(part7)}
          sub={now.voters != null ? `today ${now.voters}/${now.eligible} voted` : "no ballot today"} />
        <Kpi label="Committed capital" value={money(now.committed_capital_usd)}
          sub={`$${Math.round(now.executed_notional_usd).toLocaleString()} executed today · ${now.error_count} errors`} />
      </div>

      {s.length === 0 ? (
        <OpsCard>
          <SectionLabel>History</SectionLabel>
          <p className="text-[12.5px] text-subtle">
            No daily rows yet — the nightly aggregation (concord-daily-metrics.timer) backfills
            the whole history on its first run. Tiles above are live regardless.
          </p>
        </OpsCard>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <OpsCard>
            <SectionHead label="Member growth" />
            <MetricChart days={days} fmt={(v) => String(Math.round(v))}
              series={[
                { label: "Members", color: "var(--teal)", width: 2.2, values: s.map((d) => d.member_count) },
                { label: "Activated", color: "rgba(255,255,255,0.35)", width: 1.6, values: s.map((d) => d.activation_count) },
              ]} />
          </OpsCard>
          <OpsCard>
            <SectionHead label="Ballot participation" />
            <MetricChart days={days} fmt={(v) => `${(v * 100).toFixed(0)}%`}
              series={[
                { label: "Voted / eligible", color: "var(--teal)", width: 2.2,
                  values: s.map((d) => d.ballot_participation) },
              ]} />
          </OpsCard>
          <OpsCard className="lg:col-span-2">
            <SectionHead label="Committed capital" />
            <MetricChart days={days} fmt={(v) => money(v)}
              series={[
                { label: "Weekly-snapshot equity", color: "var(--teal)", width: 2.2,
                  values: s.map((d) => d.committed_capital_usd) },
              ]} />
          </OpsCard>
        </div>
      )}

      <OpsCard>
        <SectionHead label="Voting retention — signup cohorts"
          right={<Button variant="glass" busy={busy} onClick={load}>Reload</Button>} />
        {data.cohorts.length === 0 ? (
          <p className="text-[12.5px] text-subtle">No human members yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead>
                <tr>
                  <th className={TH}>Cohort (week of)</th>
                  <th className={`${TH} text-right`}>Size</th>
                  {data.cohorts[0].retention.map((_, i) => (
                    <th key={i} className={`${TH} text-right`}>W{i}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.cohorts.map((c) => (
                  <tr key={c.cohort} className="border-t border-hair">
                    <td className="py-1.5 font-mono text-[11.5px] tnum">{fmtDay(c.cohort)}</td>
                    <td className="py-1.5 text-right font-mono text-[11.5px] tnum">{c.size}</td>
                    {c.retention.map((r, i) => (
                      <td key={i} className="py-1.5 text-right font-mono text-[11.5px] tnum">
                        {r == null ? <span className="text-subtle">·</span> : (
                          <span style={{ color: `rgba(45,212,191,${0.35 + 0.65 * r})` }}>
                            {pct(r)}
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-[11px] leading-snug text-subtle">
          % of each signup cohort that cast a vote during week N of their own membership.
          “·” = the week hasn’t fully elapsed for that cohort yet.
        </p>
      </OpsCard>
    </div>
  );
}
