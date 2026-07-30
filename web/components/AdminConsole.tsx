"use client";

// The ops console served at admin.voteconcordia.com — one dense screen
// answering "will the next 09:31 ET run place orders, and if not, why?".
// The ONLY mutation is the arm/disarm switch (owner-requested 2026-07-28):
// arming demands a typed ARM confirmation — it is the human step that lets
// real money move. Disarming is deliberately one click: stopping must always
// be easier than starting.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { AdminPanel } from "@/lib/types";
import { Button, Card, Logo, Pill } from "./ui";
import { fmtTradeDate } from "@/lib/format";

const POLL_MS = 60_000;

// ── the one mutation on this page ───────────────────────────────────────────
// Arming = the human step that allows real orders. Typed confirmation, no
// shortcuts. Disarming is one click — stopping must always be easy.
function ArmControl({ armed, isLive, onChanged }: { armed: boolean; isLive: boolean; onChanged: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function setArmed(on: boolean) {
    setBusy(true); setErr(null);
    try {
      await api.adminArm(on);
      setConfirming(false); setTyped("");
      onChanged();
    } catch (e: any) {
      setErr(e?.message || "failed");
    } finally { setBusy(false); }
  }

  if (armed) {
    return (
      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/10 pt-4">
        <span className="text-[12.5px] text-muted">
          Trading is armed — the next eligible run places real orders.
        </span>
        <Button variant="danger" busy={busy} onClick={() => setArmed(false)}>Disarm</Button>
        {err && <span className="text-[12.5px] text-red">⚠ {err}</span>}
      </div>
    );
  }

  return (
    <div className="mt-4 border-t border-white/10 pt-4">
      {!confirming ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[12.5px] text-muted">
            {isLive
              ? "Mode is live — arming is the last gate before real orders."
              : "Mode is dry_run — arming alone won't place orders until the mode is live."}
          </span>
          <Button variant="ok" onClick={() => { setConfirming(true); setErr(null); }}>
            Arm trading…
          </Button>
          {err && <span className="text-[12.5px] text-red">⚠ {err}</span>}
        </div>
      ) : (
        <div className="space-y-2.5">
          <p className="text-[12.5px] leading-relaxed text-muted">
            This allows the club to place <b className="text-ink">real orders in every
            connected member&apos;s account</b> at the next eligible run. Type{" "}
            <b className="text-ink">ARM</b> to confirm.
          </p>
          <div className="flex flex-wrap items-center gap-2.5">
            <input
              value={typed}
              onChange={(e) => { setTyped(e.target.value); setErr(null); }}
              placeholder="ARM"
              className="input h-10 !w-28 !rounded-[12px] !py-0 text-center font-mono !text-[12px]"
              spellCheck={false} autoComplete="off"
            />
            <Button variant="ok" className="h-10" busy={busy}
              disabled={typed.trim().toUpperCase() !== "ARM"}
              onClick={() => setArmed(true)}>
              Arm trading
            </Button>
            <button onClick={() => { setConfirming(false); setTyped(""); setErr(null); }}
              className="text-[12.5px] text-subtle hover:text-ink">cancel</button>
          </div>
          {err && <div className="text-[12.5px] text-red">⚠ {err}</div>}
        </div>
      )}
    </div>
  );
}

// ── formatting helpers ──────────────────────────────────────────────────────

/** Epoch seconds (or ms, or ISO string — defensive) → "h:mm:ss AM ET". */
function fmtET(ts: number | string | null | undefined, withDate = false): string {
  if (ts == null) return "—";
  const d = typeof ts === "number" ? new Date(ts * (ts > 1e12 ? 1 : 1000)) : new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  // Deliberately NOT lib/format's helpers: the ops console must read in ET
  // (the club's trading clock), not the viewer's zone. Date shape still
  // MM/DD/YYYY — "en-US" + 2-digit month/day gives exactly that.
  const opts: Intl.DateTimeFormatOptions = {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
    ...(withDate ? { month: "2-digit", day: "2-digit", year: "numeric" } : {}),
  };
  return `${d.toLocaleString("en-US", opts)} ET`;
}

function money(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function stake(type: string | null | undefined, value: number | null | undefined): string {
  if (value == null || !type) return "—";
  return type === "percent" ? `${value}%` : money(value);
}

/** Red for hard failures, amber for everything else that's off. */
function exceptionTone(e: string): "danger" | "warn" {
  return /error|fail|revoked|expired|halt|disconnect|missing/i.test(e) ? "danger" : "warn";
}

/** club_perf_cached arrives shape-unknown — render whatever is legible. */
function perfLine(p: unknown): string | null {
  if (p == null) return null;
  if (typeof p === "string") return p;
  if (typeof p === "number") return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`;
  if (typeof p === "object") {
    const o = p as Record<string, unknown>;
    const club = o.club_pct ?? o.club;
    const spy = o.spy_pct ?? o.spy;
    const pct = (v: unknown) => (typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "—");
    if (club != null || spy != null) return `club ${pct(club)} vs SPY ${pct(spy)}`;
    try { return JSON.stringify(p); } catch { return null; }
  }
  return String(p);
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="label mb-3">{children}</p>;
}

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
  const { member, logout } = useAuth();
  const [panel, setPanel] = useState<AdminPanel | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const inFlight = useRef(false);

  const load = useCallback(async (manual: boolean) => {
    if (inFlight.current) return;           // a slow poll must never stack
    inFlight.current = true;
    if (manual) setBusy(true);
    try {
      const p = await api.adminPanel();
      setPanel(p);
      setErr(null);
      setUpdatedAt(new Date());
    } catch (e: any) {
      // stale data stays on screen — the error line says why it's stale
      setErr(e?.message || "failed to load admin panel");
    } finally {
      inFlight.current = false;
      if (manual) setBusy(false);
    }
  }, []);

  useEffect(() => {
    load(false);
    const id = setInterval(() => load(false), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const healthy = panel?.members.filter((m) => !m.exceptions?.length) ?? [];
  const troubled = panel?.members.filter((m) => m.exceptions?.length) ?? [];
  const perf = perfLine(panel?.money?.club_perf_cached);
  const audit = (panel?.audit ?? []).slice().sort((a, b) => b.ts - a.ts).slice(0, 20);

  return (
    <div className="mx-auto w-[min(1080px,100%)] px-4 pb-24 pt-8 sm:px-6">
      {/* header — no Nav on the admin host, so refresh + logout live here */}
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <Logo size={30} />
        <b className="font-display text-[15px] font-bold tracking-tight text-[#8FD9C0]">concordia</b>
        <Pill tone="teal">admin console</Pill>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-[12px] text-subtle tnum">
            {updatedAt ? `updated ${updatedAt.toLocaleTimeString(undefined, { hour12: false })}` : "loading…"}
          </span>
          <Button variant="glass" busy={busy} onClick={() => load(true)} className="!px-3.5 !py-2 !text-[12.5px]">
            Refresh
          </Button>
          <button onClick={logout}
            className="rounded-full border border-hair px-3 py-1.5 text-[12px] text-muted transition hover:text-ink">
            Log out
          </button>
        </div>
      </header>

      {err && (
        <div className="mb-4 flex items-center gap-3 rounded-[14px] border border-red/30 bg-red/[0.08] px-4 py-2.5 text-[13px] text-red">
          <span>⚠ {err}{panel ? " — showing last good data" : ""}</span>
        </div>
      )}

      {!panel ? (
        !err && (
          <div className="py-24 text-center text-[13px] text-subtle">loading admin panel…</div>
        )
      ) : (
        <div className="space-y-4">
          {/* ── THE VERDICT — the one thing this page exists to say ── */}
          <div
            className="glass glass-glow p-5 sm:p-6"
            style={panel.state.will_place_next
              ? { borderColor: "rgba(52, 227, 168, 0.4)", background: "rgba(52, 227, 168, 0.06)" }
              : { borderColor: "rgba(255, 107, 107, 0.4)", background: "rgba(255, 107, 107, 0.05)" }}
          >
            <div className="flex flex-wrap items-center gap-3">
              <div className={`font-display text-[19px] font-bold sm:text-[21px] ${panel.state.will_place_next ? "text-green" : "text-red"}`}>
                {panel.state.will_place_next
                  ? "Next 09:31 ET run WILL place orders"
                  : "Next run will NOT place orders"}
              </div>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <Pill tone={panel.state.is_live ? "teal" : "neutral"}>
                  mode <b>{panel.state.execution_mode}</b>
                </Pill>
                <Pill tone={panel.state.armed ? "ok" : "warn"}>{panel.state.armed ? "armed" : "disarmed"}</Pill>
                {panel.state.halt && <Pill tone="danger">HALT</Pill>}
              </div>
            </div>
            {!panel.state.will_place_next && panel.state.reasons?.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {panel.state.reasons.map((r, i) => (
                  <Pill key={i} tone="danger">{r}</Pill>
                ))}
              </div>
            )}
            <ArmControl armed={panel.state.armed} isLive={panel.state.is_live} onChanged={() => load(true)} />
          </div>

          {/* ── MEMBERS — healthy collapse to one line, exceptions get rows ── */}
          <Card>
            <SectionLabel>Members</SectionLabel>
            <div className="flex items-center gap-2.5 text-[13.5px]">
              <Pill tone={troubled.length === 0 ? "ok" : "warn"}>
                {healthy.length} of {panel.members.length} ready
              </Pill>
              {troubled.length > 0 && (
                <span className="text-[12.5px] text-muted">{troubled.length} need{troubled.length === 1 ? "s" : ""} attention</span>
              )}
            </div>
            {troubled.length > 0 && (
              <div className="mt-3 space-y-2">
                {troubled.map((m) => (
                  <div key={m.id} className="flex flex-wrap items-center gap-2 border-t border-hair pt-2.5">
                    <span className="font-display text-[13.5px] font-semibold">{m.display_name}</span>
                    <span className="text-[12px] text-subtle">{m.email}</span>
                    {m.role !== "member" && <Pill tone="teal">{m.role}</Pill>}
                    <span className="ml-auto flex flex-wrap justify-end gap-1.5">
                      {m.exceptions.map((e, i) => (
                        <Pill key={i} tone={exceptionTone(e)}>{e}</Pill>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            {/* ── CYCLE — today's session + the last executed run ── */}
            <Card>
              <SectionLabel>Cycle</SectionLabel>
              {panel.cycle.open ? (
                <div className="space-y-2 text-[13.5px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-display font-semibold">{fmtTradeDate(panel.cycle.open.trade_date)}</span>
                    <span className="text-muted">closes {fmtET(panel.cycle.open.closes_at)}</span>
                    <Pill tone={panel.cycle.open.quorum_met ? "ok" : "warn"}>
                      {panel.cycle.open.quorum_met ? "quorum met" : "quorum not met"}
                    </Pill>
                  </div>
                  <div className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-muted tnum">
                    <span>voters <b className="text-ink">{panel.cycle.open.voters}</b> ({panel.cycle.open.human_voters} human)</span>
                    <span>picks so far <b className="text-ink">{panel.cycle.open.picks_so_far}</b></span>
                    <span>bots voted <b className="text-ink">{panel.cycle.bots.voted_today}</b>/{panel.cycle.bots.count}</span>
                  </div>
                </div>
              ) : (
                <div className="text-[13.5px] text-muted">
                  no open session
                  <span className="ml-4 text-[13px] tnum">bots voted <b className="text-ink">{panel.cycle.bots.voted_today}</b>/{panel.cycle.bots.count}</span>
                </div>
              )}

              <div className="mt-4 border-t border-hair pt-3.5">
                <SectionLabel>Last run</SectionLabel>
                {panel.cycle.last_executed ? (
                  <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
                    <span className="font-display font-semibold">{fmtTradeDate(panel.cycle.last_executed.trade_date)}</span>
                    <span className="flex flex-wrap gap-x-3 text-[13px] text-muted tnum">
                      {Object.entries(panel.cycle.last_executed.by_status).map(([k, v]) => (
                        <span key={k} className={k === "error" && v > 0 ? "font-semibold text-red" : ""}>
                          {k} <b className={k === "error" && v > 0 ? "text-red" : "text-ink"}>{v}</b>
                        </span>
                      ))}
                    </span>
                    {panel.cycle.last_executed.latest_verify_verdict && (
                      <span className="ml-auto">
                        <Pill tone={
                          panel.cycle.last_executed.latest_verify_verdict === "PASS" ? "ok"
                            : panel.cycle.last_executed.latest_verify_verdict === "WARN" ? "warn"
                            : panel.cycle.last_executed.latest_verify_verdict === "FAIL" ? "danger" : "neutral"
                        }>
                          verify {panel.cycle.last_executed.latest_verify_verdict}
                        </Pill>
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="text-[13.5px] text-muted">no executed run yet</div>
                )}
              </div>
            </Card>

            {/* ── MONEY — stake + voting power per member ── */}
            <Card>
              <SectionLabel>Money</SectionLabel>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left">
                    <th className="label pb-2 font-semibold">member</th>
                    <th className="label pb-2 text-right font-semibold">stake</th>
                    <th className="label pb-2 text-right font-semibold">power</th>
                  </tr>
                </thead>
                <tbody>
                  {panel.money.per_member.map((m) => (
                    <tr key={m.id} className="border-t border-hair">
                      <td className="py-2 pr-3 font-display font-semibold">{m.display_name}</td>
                      <td className="py-2 text-right text-muted tnum">{stake(m.stake_type, m.stake_value)}</td>
                      <td className="py-2 text-right tnum">{money(m.voting_power)}</td>
                    </tr>
                  ))}
                  {panel.money.per_member.length === 0 && (
                    <tr><td colSpan={3} className="py-2 text-muted">no members</td></tr>
                  )}
                </tbody>
              </table>
              {perf && (
                <p className="mt-3 border-t border-hair pt-2.5 text-[12.5px] text-muted tnum">
                  club performance (cached): {perf}
                </p>
              )}
            </Card>
          </div>

          {/* ── AUDIT — last 20, newest first ── */}
          <Card>
            <SectionLabel>Audit</SectionLabel>
            {audit.length === 0 ? (
              <div className="text-[13px] text-muted">no audit entries</div>
            ) : (
              <div className="space-y-1 overflow-x-auto">
                {audit.map((a, i) => (
                  <div key={i} className="flex gap-3 whitespace-nowrap font-mono text-[11.5px] leading-relaxed">
                    <span className="text-subtle tnum">{fmtET(a.ts, true)}</span>
                    <span className="text-teal">{a.actor}</span>
                    <span className="text-ink">{a.action}</span>
                    {a.target && <span className="text-muted">{a.target}</span>}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <p className="text-center text-[11px] text-subtle">
            read-only console · signed in as {member?.email} · auto-refreshes every 60s
          </p>
        </div>
      )}
    </div>
  );
}
