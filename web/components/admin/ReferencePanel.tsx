"use client";

// /reference — the Alpaca reference account.
//
// One operator-owned Alpaca account that mirrors the club's decided basket, so
// the track record is MEASURED rather than reconstructed out of members'
// commingled Robinhood accounts. Members are unaffected: they stay on
// Robinhood and nobody re-onboards.
//
// This page is deliberately explicit about the three arming gates. "A key is
// present" must never read as "it may trade", so each gate renders as its own
// chip and the headline verdict is computed from all of them.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AdminReference } from "@/lib/api";
import { Button } from "@/components/ui";
import { OpsCard, SectionHead, SectionLabel, money } from "./util";

function Chip({ on, label, offLabel }: { on: boolean; label: string; offLabel?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-semibold ${
        on ? "bg-teal/15 text-teal" : "bg-white/[0.06] text-subtle"
      }`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${on ? "bg-teal" : "bg-white/30"}`} />
      {on ? label : offLabel ?? label}
    </span>
  );
}

function Setup() {
  return (
    <div className="mt-3 space-y-3 text-[12.5px] leading-relaxed text-subtle">
      <p>
        No Alpaca key pair is configured. Create one at{" "}
        <span className="text-ink">alpaca.markets</span> → API Keys, then add it to{" "}
        <code className="rounded bg-white/[0.06] px-1 py-0.5">/opt/concord/.env</code> on the
        box and restart the API. Keys live only in that file (chmod 600) — never in the
        database and never in this console.
      </p>
      <pre className="overflow-x-auto rounded-[10px] border border-white/10 bg-black/30 p-3 text-[11.5px] text-ink">
{`ALPACA_KEY_ID=<your key id>
ALPACA_SECRET_KEY=<your secret>
ALPACA_PAPER=1          # 1 = paper, 0 = live money
REFERENCE_ENABLED=0     # 1 lets the mirror run (still plans only)
REFERENCE_ARMED=0       # 1 lets it actually place — arm this LAST
REFERENCE_BUDGET_USD=0  # dollars deployed across the basket`}
      </pre>
      <p>
        Start on paper. Nothing places an order until{" "}
        <span className="text-ink">every</span> gate above is on and the budget is funded.
      </p>
    </div>
  );
}

export function ReferencePanel() {
  const [d, setD] = useState<AdminReference | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [out, setOut] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    api.adminReference()
      .then(setD)
      .catch((e) =>
        setErr(e instanceof ApiError && e.status === 404
          ? "This backend predates the reference account endpoint."
          : "Could not load the reference account."));
  }, []);

  useEffect(load, [load]);

  const act = (what: "dry" | "snapshot") => {
    setBusy(what); setOut(null);
    const p = what === "snapshot" ? api.adminReferenceSnapshot() : api.adminReferenceRun(true);
    p.then((r) => { setOut(JSON.stringify(r, null, 2)); load(); })
      .catch((e) => setOut(String(e?.message ?? e)))
      .finally(() => setBusy(null));
  };

  if (err) return <OpsCard><p className="text-[12.5px] text-subtle">{err}</p></OpsCard>;
  if (!d) return <div className="pulse h-[220px] rounded-[12px] bg-white/[0.04]" />;

  const probe = d.probe ?? {};
  return (
    <div className="space-y-4">
      <OpsCard>
        <SectionHead
          label="Reference account (Alpaca)"
          right={<Button onClick={load} variant="ghost">Refresh</Button>}
        />
        <p className="mb-3 text-[12.5px] leading-relaxed text-subtle">
          One account you own, holding only the club&apos;s basket. Its record is the
          strategy&apos;s — <span className="text-ink">not</span> what members made, which
          differs through cash drag, T+1 deferrals and fill timing.
        </p>

        <div className="flex flex-wrap gap-2">
          <Chip on={!!d.configured} label="Key configured" offLabel="No key" />
          <Chip on={!d.paper} label="LIVE money" offLabel="Paper" />
          <Chip on={!!d.enabled} label="Mirror enabled" offLabel="Mirror off" />
          <Chip on={!!d.armed} label="Armed" offLabel="Disarmed" />
          <Chip on={!!d.will_place} label="WILL PLACE ORDERS" offLabel="Places nothing" />
        </div>

        {!d.configured ? <Setup /> : (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <SectionLabel>Connection</SectionLabel>
              <p className="text-[12.5px] text-subtle">
                {probe.reachable
                  ? `${probe.status ?? "ok"} · acct ${probe.account_number ?? "—"}`
                  : `unreachable — ${probe.detail ?? "unknown"}`}
              </p>
              {probe.reachable && (
                <p className="mt-1 text-[12.5px] text-subtle">
                  equity {money(Number(probe.equity ?? 0))} · cash {money(Number(probe.cash ?? 0))}
                </p>
              )}
              <p className="mt-1 text-[12.5px] text-subtle">
                budget/cycle {d.budget_usd ? money(d.budget_usd) : "not funded"}
              </p>
            </div>
            <div>
              <SectionLabel>Record</SectionLabel>
              <p className="text-[12.5px] text-subtle">
                {d.days_recorded
                  ? `${d.days_recorded} days since ${d.start_date}`
                  : "no snapshots yet"}
              </p>
              {d.club_pct != null && (
                <p className="mt-1 text-[13px] font-semibold tnum">
                  <span className="text-teal">{d.club_pct >= 0 ? "+" : ""}{d.club_pct}%</span>
                  <span className="text-subtle"> vs S&amp;P {d.spy_pct != null
                    ? `${d.spy_pct >= 0 ? "+" : ""}${d.spy_pct}%` : "—"}</span>
                </p>
              )}
            </div>
          </div>
        )}
      </OpsCard>

      {d.configured && (
        <OpsCard>
          <SectionHead label="Actions" />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => act("dry")} disabled={busy !== null}>
              {busy === "dry" ? "Planning…" : "Dry run (plan only)"}
            </Button>
            <Button onClick={() => act("snapshot")} disabled={busy !== null} variant="ghost">
              {busy === "snapshot" ? "Pulling…" : "Pull snapshot"}
            </Button>
          </div>
          <p className="mt-2 text-[12.5px] text-subtle">
            A dry run shows exactly what it would send and places nothing. Live runs happen
            only on the schedule, and only when every gate above is on.
          </p>
          {out && (
            <pre className="mt-3 max-h-[320px] overflow-auto rounded-[10px] border border-white/10 bg-black/30 p-3 text-[11.5px] text-ink">
              {out}
            </pre>
          )}
        </OpsCard>
      )}
    </div>
  );
}
