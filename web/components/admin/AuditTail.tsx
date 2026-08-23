"use client";

// The console's paper trail, two ways:
//
//  · AuditLines — the bare mono log lines the Overview shows as a 5-line
//    teaser (fed by panel.audit; unchanged export, other pages depend on it).
//  · AuditPage — the /audit browser: the full trail via GET /api/admin/audit
//    with action-group filter chips, an actor column, expandable
//    pretty-printed detail_json, and before=<id> "load more" paging — plus
//    the Sign-ins card (GET /api/admin/logins: who, when, ip, kind).
//
// Degrades: a backend that predates the new endpoints 404s → the browser
// falls back to panel.audit's last-20 lines behind a dim note, and the
// Sign-ins card dims. The page never blanks.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AdminAuditEntry, type AdminLoginRow } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Button, Pill } from "@/components/ui";
import { OpsCard, SectionHead, SectionLabel, TH_FONT as TH, fmtET } from "./util";

// ── the Overview teaser (unchanged contract) ────────────────────────────────

export function AuditLines({ audit, limit = 20 }: {
  audit: AdminPanel["audit"];
  limit?: number;
}) {
  const rows = (audit ?? []).slice().sort((a, b) => b.ts - a.ts).slice(0, limit);
  if (rows.length === 0) {
    return <div className="text-[12.5px] text-muted">no audit entries</div>;
  }
  return (
    <div className="space-y-[3px] overflow-x-auto">
      {rows.map((a, i) => (
        <div key={i} className="flex gap-3 whitespace-nowrap font-mono text-[11px] leading-normal">
          <span className="w-[158px] flex-none text-subtle tnum">{fmtET(a.ts, true)}</span>
          <span className="w-[118px] flex-none truncate text-teal" title={a.actor}>{a.actor}</span>
          <span className="text-ink">{a.action}</span>
          {a.target && <span className="text-muted">{a.target}</span>}
        </div>
      ))}
    </div>
  );
}

// ── action grouping (client-side filter over loaded pages) ──────────────────

type GroupKey = "all" | "trading" | "members" | "system";

const TRADING = new Set([
  "execute_start", "execute_done", "execute_halted", "manual_trigger",
  "verify_done", "arm", "kill", "member_skipped", "buy_refused_stale_basket",
  "reconcile", "reconcile_all", "dust_cleanup", "research_vote",
  "bot_votes", "bot_reweight", "derived_negative_net",
]);
const MEMBERS = new Set([
  "signup", "vote", "participate", "allocation", "reset_password",
  "set_password", "change_password", "display_name", "email_change",
  "email_verified", "google_link", "google_link_password_disabled",
  "logout_all", "account_deleted", "api_key_create", "api_key_revoke",
  "margin_ack", "rh_connect", "rh_disconnect", "rh_oauth_start",
  "rh_oauth_error", "rh_reauth_required", "rh_reset",
]);
const SYSTEM = new Set([
  "open_session", "close_session", "config_changed", "rename_detected",
  "symbol_migrated", "weekly_power", "blended_power", "accuracy_scored",
  "seed_bots", "mail_error", "perf_fetch_error",
]);

/** Unknown actions (a newer backend's inventions) classify by actor, so
 *  nothing ever vanishes from every group. */
function groupOf(e: { actor: string; action: string }): Exclude<GroupKey, "all"> {
  if (TRADING.has(e.action)) return "trading";
  if (MEMBERS.has(e.action)) return "members";
  if (SYSTEM.has(e.action)) return "system";
  if (e.actor?.startsWith("member:")) return "members";
  if (e.actor === "system" || e.actor === "mailer") return "system";
  return "trading"; // admin-actor unknowns are almost always lever pulls
}

const GROUPS: { key: GroupKey; label: string }[] = [
  { key: "all", label: "all" },
  { key: "trading", label: "trading" },
  { key: "members", label: "members" },
  { key: "system", label: "system" },
];

// ── the sign-ins card ───────────────────────────────────────────────────────

function SignInsCard() {
  const [logins, setLogins] = useState<AdminLoginRow[] | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    api.adminLogins()
      .then((r) => { if (!dead) setLogins(r.logins); })
      .catch((e) => {
        if (dead) return;
        setNote(e instanceof ApiError && e.status === 404
          ? "sign-in history needs the newer backend"
          : e instanceof Error ? e.message : String(e));
      });
    return () => { dead = true; };
  }, []);

  return (
    <OpsCard>
      <SectionLabel>Sign-ins — last 30</SectionLabel>
      {note ? (
        <p className="text-[12px] text-subtle">{note}</p>
      ) : !logins ? (
        <p className="text-[12px] text-subtle">loading…</p>
      ) : logins.length === 0 ? (
        <p className="text-[12px] text-subtle">no sign-ins recorded</p>
      ) : (
        <div className="space-y-2">
          {logins.map((l, i) => (
            <div key={i} className="border-t border-hair pt-1.5 text-[11.5px] leading-snug first:border-t-0 first:pt-0">
              <div className="flex items-center gap-2">
                <span className="min-w-0 truncate font-medium text-ink" title={l.email || undefined}>
                  {l.display_name || l.email || `member ${l.member_id}`}
                </span>
                <span className="ml-auto flex-none">
                  <Pill tone={l.kind === "signup" ? "teal" : "neutral"}>{l.kind || "login"}</Pill>
                </span>
              </div>
              <div className="mt-0.5 flex gap-2 font-mono text-[10.5px] text-subtle tnum">
                <span>{fmtET(l.ts, true)}</span>
                {l.ip && <span className="truncate">{l.ip}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </OpsCard>
  );
}

// ── the /audit page ─────────────────────────────────────────────────────────

const PAGE = 50; // mirrors the endpoint's default limit

export function AuditPage({ panelAudit }: { panelAudit: AdminPanel["audit"] }) {
  const [entries, setEntries] = useState<AdminAuditEntry[] | null>(null);
  const [missing, setMissing] = useState(false);   // endpoint 404 = older backend
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busyMore, setBusyMore] = useState(false);
  const [group, setGroup] = useState<GroupKey>("all");
  const [open, setOpen] = useState<Set<number>>(new Set());

  const load = useCallback(async (before?: number) => {
    if (before != null) setBusyMore(true);
    try {
      const r = await api.adminAudit(before != null ? { before } : {});
      setEntries((prev) => (before != null && prev ? [...prev, ...r.entries] : r.entries));
      setDone(r.entries.length < PAGE);
      setErr(null);
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (before != null) setBusyMore(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function toggle(id: number) {
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const counts: Record<GroupKey, number> = { all: 0, trading: 0, members: 0, system: 0 };
  for (const e of entries ?? []) { counts.all++; counts[groupOf(e)]++; }
  const shown = (entries ?? []).filter((e) => group === "all" || groupOf(e) === group);

  return (
    <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr),320px]">
      <OpsCard>
        <SectionHead label="Audit trail"
          right={entries?.length
            ? <span className="font-mono text-[11px] tnum text-subtle">{entries.length} loaded</span>
            : undefined} />

        {missing ? (
          <>
            <p className="mb-3 text-[12px] text-subtle">
              full audit browser unavailable — this backend predates
              /api/admin/audit. Showing the panel&apos;s last 20 lines instead.
            </p>
            <AuditLines audit={panelAudit} />
          </>
        ) : err && !entries ? (
          <p className="text-[12.5px] text-red">⚠ {err}</p>
        ) : !entries ? (
          <p className="text-[12px] text-subtle">loading audit trail…</p>
        ) : (
          <>
            {/* group chips — client-side over the loaded pages */}
            <div className="mb-2.5 flex flex-wrap gap-1.5">
              {GROUPS.map((g) => {
                const on = g.key === group;
                return (
                  <button key={g.key} onClick={() => setGroup(g.key)}
                    aria-pressed={on}
                    className={`rounded-full border px-2.5 py-[5px] font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em] transition ${
                      on ? "border-teal/40 bg-teal/[0.1] text-teal"
                        : "border-hair bg-white/[0.03] text-subtle hover:text-ink"
                    }`}>
                    {g.label} <span className="tnum">{counts[g.key]}</span>
                  </button>
                );
              })}
            </div>

            {err && <p className="mb-2 text-[12px] text-red">⚠ {err} — showing loaded rows</p>}

            {shown.length === 0 ? (
              <p className="text-[12px] text-subtle">no {group === "all" ? "" : `${group} `}entries in the loaded pages — load more below</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-[12px]">
                  <thead>
                    <tr>
                      <th className={`${TH} pr-3 text-left`}>time</th>
                      <th className={`${TH} pr-3 text-left`}>actor</th>
                      <th className={`${TH} pr-3 text-left`}>action</th>
                      <th className={`${TH} pr-3 text-left`}>target</th>
                      <th className={`${TH} text-right`}>detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((e) => {
                      const has = e.detail != null;
                      const on = has && open.has(e.id);
                      return [
                        <tr key={e.id}
                          onClick={has ? () => toggle(e.id) : undefined}
                          aria-expanded={has ? on : undefined}
                          className={`border-t border-hair font-mono text-[11px] ${
                            has ? "cursor-pointer transition hover:bg-white/[0.025]" : ""
                          } ${on ? "bg-white/[0.02]" : ""}`}>
                          <td className="whitespace-nowrap py-1.5 pr-3 text-subtle tnum">{fmtET(e.ts, true)}</td>
                          <td className="max-w-[150px] truncate py-1.5 pr-3 text-teal" title={e.actor}>{e.actor}</td>
                          <td className="whitespace-nowrap py-1.5 pr-3 text-ink">{e.action}</td>
                          <td className="max-w-[180px] truncate py-1.5 pr-3 text-muted" title={e.target || undefined}>{e.target || ""}</td>
                          <td className="py-1.5 text-right">
                            {has && (
                              <span className={`inline-block text-[10px] text-subtle transition-transform ${on ? "rotate-90" : ""}`} aria-hidden>▸</span>
                            )}
                          </td>
                        </tr>,
                        on ? (
                          <tr key={`${e.id}-detail`} className="bg-white/[0.015]">
                            <td colSpan={5} className="px-2 pb-2 pt-1">
                              <pre className="max-h-64 overflow-auto rounded-[10px] border border-hair bg-black/25 p-3 font-mono text-[10.5px] leading-relaxed text-muted">{JSON.stringify(e.detail, null, 2)}</pre>
                            </td>
                          </tr>
                        ) : null,
                      ];
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {!done && entries.length > 0 && (
              <div className="mt-3 flex justify-center">
                <Button variant="glass" busy={busyMore}
                  onClick={() => load(entries[entries.length - 1]?.id)}
                  className="!px-3 !py-1.5 !text-[12px]">
                  Load more
                </Button>
              </div>
            )}
          </>
        )}
      </OpsCard>

      <SignInsCard />
    </div>
  );
}
