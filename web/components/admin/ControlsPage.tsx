"use client";

// /controls — the club's levers, promoted to their own full page (owner call:
// the verdict-plus-buttons panel was eating the Overview). Layout: a verdict
// hero that says whether the next run places orders and why not, then one
// card per lever. EVERY action confirms — inline, never window.confirm:
//   · Arm            — typed "ARM" (unchanged: the human gate for real money)
//   · Execute live   — typed "EXECUTE" (real orders, the strongest gate)
//   · Disarm / Kill / Close vote / Dry-run — two-step click ("Confirm …")
// Stopping stays easier than starting: the dangerous stops are two clicks,
// the dangerous starts are typed.

import { useState } from "react";
import { api, type AdminRunResult } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Button, Pill } from "@/components/ui";
import { OpsCard } from "./util";

// ── run-result digestion (carried over from the old StatusBar) ─────────────
function summarizeRun(r: AdminRunResult): string {
  const parts: string[] = [];
  if (typeof r.mode === "string") parts.push(`mode ${r.mode}`);
  if (Array.isArray(r.orders)) {
    const by: Record<string, number> = {};
    for (const o of r.orders) {
      const k = o?.status || "?";
      by[k] = (by[k] || 0) + 1;
    }
    const line = Object.entries(by).map(([k, v]) => `${v} ${k}`).join(" · ");
    parts.push(line || "no orders");
  }
  if (typeof r.placed_total === "number") parts.push(`${r.placed_total} placed total`);
  if (r.skipped && typeof r.skipped === "object") {
    const s = Object.entries(r.skipped).map(([k, v]) => `${v}× ${k}`).join(", ");
    if (s) parts.push(`skipped: ${s}`);
  }
  if (typeof r.note === "string" && r.note) parts.push(r.note);
  return parts.join(" — ") || "done";
}

/** Two-step inline confirmation: first click arms the button, second fires.
 *  `word` upgrades it to a typed gate (ARM / EXECUTE). */
function ConfirmButton({ label, confirmLabel, variant, word, busy, disabled, onFire }: {
  label: string;
  confirmLabel?: string;
  variant: "ok" | "danger" | "glass";
  /** require typing this word (uppercase) instead of a second click */
  word?: string;
  busy?: boolean;
  disabled?: boolean;
  onFire: () => void;
}) {
  const [arming, setArming] = useState(false);
  const [typed, setTyped] = useState("");

  if (!arming) {
    return (
      <Button variant={variant} busy={busy} disabled={disabled}
        className="!px-3.5 !py-2 !text-[12.5px]"
        onClick={() => { setArming(true); setTyped(""); }}>
        {label}
      </Button>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      {word && (
        <input
          autoFocus value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={word}
          className="input h-9 !w-28 !rounded-[10px] !py-0 text-center font-mono !text-[12px]"
          spellCheck={false} autoComplete="off"
        />
      )}
      <Button variant={variant} busy={busy}
        disabled={disabled || (word ? typed.trim().toUpperCase() !== word : false)}
        className="!px-3.5 !py-2 !text-[12.5px]"
        onClick={() => { setArming(false); onFire(); }}>
        {confirmLabel ?? `Confirm ${label.toLowerCase()}`}
      </Button>
      <button onClick={() => setArming(false)}
        className="text-[12px] text-subtle hover:text-ink">cancel</button>
    </span>
  );
}

function ControlCard({ title, desc, children }: {
  title: string; desc: string; children: React.ReactNode;
}) {
  return (
    <OpsCard className="flex min-h-[118px] flex-col">
      <div className="ops-label">{title}</div>
      <p className="mt-1.5 flex-1 text-[12px] leading-snug text-muted">{desc}</p>
      <div className="mt-3">{children}</div>
    </OpsCard>
  );
}

// ── the page ────────────────────────────────────────────────────────────────
export function ControlsPage({ state, cadence, inviteCode, onChanged }: {
  state: AdminPanel["state"];
  cadence: string | null;
  inviteCode?: string | null;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function act(key: string, fn: () => Promise<AdminRunResult | Record<string, unknown>>, label: string) {
    setBusy(key); setErr(null); setResult(null);
    try {
      const r = await fn();
      setResult(`${label}: ${summarizeRun(r as AdminRunResult)}`);
      onChanged();
    } catch (e: any) {
      setErr(`${label} failed — ${e?.message || "unknown error"}`);
    } finally { setBusy(null); }
  }

  async function copyInvite() {
    if (!inviteCode) return;
    try { await navigator.clipboard.writeText(inviteCode); setCopied(true); }
    catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = inviteCode; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); ta.remove(); setCopied(true);
      } catch { /* the Members page has the fallback card */ }
    }
    setTimeout(() => setCopied(false), 1500);
  }

  const canLive = state.is_live && state.armed && !state.halt;

  return (
    <div className="space-y-3">
      {/* verdict hero — the one question this page answers */}
      <div
        className="glass glass-glow p-4 sm:p-5"
        style={state.will_place_next
          ? { borderColor: "rgba(52, 227, 168, 0.4)", background: "rgba(52, 227, 168, 0.06)" }
          : { borderColor: "rgba(255, 107, 107, 0.4)", background: "rgba(255, 107, 107, 0.05)" }}
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className={`font-display text-[19px] font-bold sm:text-[22px] ${state.will_place_next ? "text-green" : "text-red"}`}>
            {state.will_place_next ? "Next run WILL place orders" : "Next run will NOT place orders"}
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Pill tone={state.is_live ? "teal" : "neutral"}>mode <b>{state.execution_mode}</b></Pill>
            <Pill tone={state.armed ? "ok" : "warn"}>{state.armed ? "armed" : "disarmed"}</Pill>
            {state.halt && <Pill tone="danger">HALT</Pill>}
            {cadence && <Pill tone="neutral">cadence <b>{cadence}</b></Pill>}
            {inviteCode && (
              <button onClick={copyInvite} title="Copy invite code" className="pill pill-teal">
                invite <b className="font-mono">{copied ? "copied ✓" : inviteCode}</b>
              </button>
            )}
          </div>
        </div>
        {!state.will_place_next && state.reasons?.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-2">
            {state.reasons.map((r, i) => <Pill key={i} tone="danger">{r}</Pill>)}
          </div>
        )}
        {(result || err) && (
          <div className={`mt-3 border-t border-hair pt-2.5 text-[12.5px] tnum ${err ? "text-red" : "text-muted"}`}>
            {err ? `⚠ ${err}` : result}
          </div>
        )}
      </div>

      {/* one card per lever — every action confirms */}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {state.armed ? (
          <ControlCard title="Disarm"
            desc="Stops the next run from placing real orders. Votes and dry-runs continue; re-arming needs the typed gate.">
            <ConfirmButton label="Disarm" variant="danger" busy={busy === "arm"}
              onFire={() => act("arm", () => api.adminArm(false), "Disarm")} />
          </ControlCard>
        ) : (
          <ControlCard title="Arm trading"
            desc={state.is_live
              ? "The last gate before real orders: the next eligible run trades every connected, opted-in account."
              : "Mode is dry_run — arming alone places nothing until the mode is live."}>
            <ConfirmButton label="Arm trading…" confirmLabel="Arm trading" variant="ok"
              word="ARM" busy={busy === "arm"}
              onFire={() => act("arm", () => api.adminArm(true), "Arm")} />
          </ControlCard>
        )}

        <ControlCard title="Close vote"
          desc="Locks today's ballot now and computes the consensus basket ahead of the scheduled close.">
          <ConfirmButton label="Close vote" variant="glass" busy={busy === "close"}
            onFire={() => act("close", () => api.adminRun("close"), "Close vote")} />
        </ControlCard>

        <ControlCard title="Dry-run"
          desc="Simulates today's phase for every member — full sizing and order plan, zero orders sent.">
          <ConfirmButton label="Dry-run" variant="glass" busy={busy === "dry"}
            onFire={() => act("dry", () => api.adminRun("execute", true), "Dry-run")} />
        </ControlCard>

        <ControlCard title="Execute live"
          desc="Places REAL market orders for today's phase on every live, armed account — right now, not at the scheduled time.">
          <ConfirmButton label="Execute live" confirmLabel="Execute live" variant="danger"
            word="EXECUTE" busy={busy === "live"} disabled={!canLive}
            onFire={() => act("live", () => api.adminRun("execute", false), "Execute (live)")} />
          {!canLive && (
            <p className="mt-2 text-[11px] text-subtle">needs live mode + armed, without halt</p>
          )}
        </ControlCard>

        <ControlCard title="Kill"
          desc="Halt + disarm in one motion. Nothing trades until you clear the halt and re-arm.">
          <ConfirmButton label="Kill" variant="danger" busy={busy === "kill"}
            onFire={() => act("kill", () => api.adminKill(), "Kill")} />
        </ControlCard>
      </div>
    </div>
  );
}
