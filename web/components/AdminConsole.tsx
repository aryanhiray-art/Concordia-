"use client";

// The ops console served at admin.voteconcordia.com — the club's whole
// operational surface. It answers "will the next 09:31 ET run place orders,
// and if not, why?" and carries the levers to change the answer: arm/disarm
// (typed ARM confirm), kill, close-vote, dry-run, live execute, per-member
// drilldown with password recovery, and the morning digest on demand.
//
// Multi-page: AppShell renders <AdminConsole /> for EVERY pathname on the
// admin host, so this component reads usePathname() and routes internally —
// / (overview) · /members · /cycle · /bots · /audit, unknown paths falling
// back to Overview. Data fetching stays HOISTED here (mount + 60s poll +
// window focus) and flows down into the page layouts, so switching tabs never
// refetches and a slow poll never stacks.
//
// Freshness contract: /api/admin/panel is the page (its failure is the page's
// error, stale data stays on screen with a why-it's-stale line); overview /
// schedule / bots are enrichments that degrade to per-panel notes — a backend
// that predates those endpoints must dim a panel, never blank the console.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { api, type AdminBots, type AdminOverview, type AdminSchedule } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { AdminPanel } from "@/lib/types";
import { Button, Card, Logo } from "./ui";
import { OpsHeader, pageKeyFor } from "./admin/OpsHeader";
import { OverviewPage } from "./admin/OverviewPage";
import { BasketPage } from "./admin/BasketPage";
import { CyclePanel } from "./admin/CyclePanel";
import { MembersPanel } from "./admin/MembersPanel";
import { MemberDrawer } from "./admin/MemberDrawer";
import { SessionPanel } from "./admin/SessionPanel";
import { BotsPanel } from "./admin/BotsPanel";
import { AuditTail } from "./admin/AuditTail";
import { AccessCard } from "./admin/AccessCard";

const POLL_MS = 60_000;

// ── access-denied interstitial (logged in, but not owner/admin) ─────────────

export function AdminDenied() {
  const { member, logout } = useAuth();
  return (
    <div className="grid min-h-screen place-items-center px-5">
      <div className="w-[min(440px,100%)]">
        <div className="mb-6 flex flex-col items-center text-center">
          <Logo size={48} />
          <div className="mt-3 font-display text-[22px] font-bold tracking-tight text-[#8FD9C0]">concordia</div>
        </div>
        <Card className="glass-glow">
          <div className="font-display text-[17px] font-bold">Admin console</div>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            This is Concordia&apos;s ops console for club owners and admins.
            {" "}<span className="text-ink">{member?.email}</span> doesn&apos;t have admin access —
            if you think it should, ask the club owner.
          </p>
          <div className="mt-5 flex items-center gap-3">
            <Button variant="glass" onClick={logout}>Log out</Button>
            <a href="https://voteconcordia.com"
              className="text-[13px] font-medium text-teal hover:underline">
              Go to the member site →
            </a>
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── the console ─────────────────────────────────────────────────────────────

export function AdminConsole() {
  const { logout } = useAuth();
  const pathname = usePathname();
  const [panel, setPanel] = useState<AdminPanel | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [schedule, setSchedule] = useState<AdminSchedule | null>(null);
  const [bots, setBots] = useState<AdminBots | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [overviewErr, setOverviewErr] = useState<string | null>(null);
  const [scheduleErr, setScheduleErr] = useState<string | null>(null);
  const [botsErr, setBotsErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const inFlight = useRef(false);
  // Stable identity: the drawer keys its keydown listener on onClose, so an
  // inline arrow here would re-bind it on every 60s poll re-render.
  const closeDrawer = useCallback(() => setSelected(null), []);

  const load = useCallback(async (manual: boolean) => {
    if (inFlight.current) return;           // a slow poll must never stack
    inFlight.current = true;
    if (manual) setBusy(true);
    const msg = (r: PromiseRejectedResult) =>
      r.reason instanceof Error ? r.reason.message : String(r.reason ?? "failed");
    try {
      const [p, o, s, b] = await Promise.allSettled([
        api.adminPanel(), api.adminOverview(), api.adminSchedule(), api.adminBots(),
      ]);
      if (p.status === "fulfilled") {
        setPanel(p.value);
        setErr(null);
        setUpdatedAt(new Date());
      } else {
        // stale data stays on screen — the error line says why it's stale
        setErr(msg(p) || "failed to load admin panel");
      }
      if (o.status === "fulfilled") { setOverview(o.value); setOverviewErr(null); }
      else setOverviewErr(msg(o));
      if (s.status === "fulfilled") { setSchedule(s.value); setScheduleErr(null); }
      else setScheduleErr(msg(s));
      if (b.status === "fulfilled") { setBots(b.value); setBotsErr(null); }
      else setBotsErr(msg(b));
    } finally {
      inFlight.current = false;
      if (manual) setBusy(false);
    }
  }, []);

  useEffect(() => {
    load(false);
    const id = setInterval(() => load(false), POLL_MS);
    // coming back to the tab after a while = look again (inFlight dedupes)
    const onFocus = () => load(false);
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  const refresh = useCallback(() => load(true), [load]);
  const page = pageKeyFor(pathname);
  const cadence = schedule?.cadence ?? overview?.schedule?.cadence ?? null;

  return (
    <div className="ops min-h-screen pb-14">
      <OpsHeader page={page} updatedAt={updatedAt} busy={busy}
        onRefresh={refresh} onLogout={logout} />

      <main className="mx-auto w-[min(1180px,100%)] px-4 pt-3.5">
        {err && (
          <div className="mb-3 flex items-center gap-3 rounded-[12px] border border-red/30 bg-red/[0.08] px-3.5 py-2 text-[12.5px] text-red">
            <span>⚠ {err}{panel ? " — showing last good data" : ""}</span>
          </div>
        )}

        {!panel ? (
          !err && (
            <div className="py-24 text-center text-[13px] text-subtle">loading admin panel…</div>
          )
        ) : (
          <div key={page} className="space-y-3">
            {page === "overview" && (
              <OverviewPage panel={panel} schedule={schedule} scheduleErr={scheduleErr}
                bots={bots} botsErr={botsErr} cadence={cadence} onChanged={refresh} />
            )}

            {page === "basket" && (
              <BasketPage panel={panel} overview={overview} overviewErr={overviewErr} />
            )}

            {page === "members" && (
              <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr),300px]">
                <MembersPanel
                  members={panel.members}
                  bots={bots}
                  botsErr={botsErr}
                  perf={panel.money?.club_perf_cached}
                  onOpen={setSelected}
                />
                <AccessCard overview={overview} err={overviewErr} />
              </div>
            )}

            {page === "cycle" && (
              <>
                <CyclePanel cycle={panel.cycle} schedule={schedule} scheduleErr={scheduleErr} />
                <SessionPanel overview={overview} err={overviewErr} />
              </>
            )}

            {page === "bots" && (
              <BotsPanel bots={bots} err={botsErr} onOpen={setSelected} />
            )}

            {page === "audit" && <AuditTail audit={panel.audit} />}
          </div>
        )}
      </main>

      {selected != null && <MemberDrawer memberId={selected} onClose={closeDrawer} />}
    </div>
  );
}
