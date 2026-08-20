"use client";

// The console's verdict + every ops control. The headline answers the one
// question this host exists for — "will the next 09:31 ET run place orders,
// and if not, why?" — and the controls underneath are the levers that change
// the answer. Arming demands a typed ARM confirmation (the human step that
// lets real money move); a live execute demands a spelled-out confirm; Kill
// and Disarm are one click — stopping must always be easier than starting.

import { useState } from "react";
import { api, type AdminRunResult } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Button, Pill } from "@/components/ui";

// ── arming — typed confirmation, no shortcuts ───────────────────────────────
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
    // no sentence — the "armed" pill says it; the button is the control
    return (
      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-white/10 pt-3">
        <Button variant="danger" busy={busy} className="!px-3.5 !py-2 !text-[12.5px]"
          onClick={() => setArmed(false)}>Disarm</Button>
        {err && <span className="text-[12.5px] text-red">⚠ {err}</span>}
      </div>
    );
  }

  return (
    <div className="mt-3 border-t border-white/10 pt-3">
      {!confirming ? (
        <div className="flex flex-wrap items-center gap-3">
          {!isLive && (
            <span className="text-[12.5px] text-muted">
              dry_run mode — arming alone places nothing
            </span>
          )}
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

// ── run-result digestion ────────────────────────────────────────────────────
/** Phase results are phase-shaped — count what looks like orders, pass through
 *  the notes, and never crash on a shape we haven't met. */
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

// ── the bar ─────────────────────────────────────────────────────────────────
export function StatusBar({ state, cadence, onChanged }: {
  state: AdminPanel["state"];
  cadence: string | null;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

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

  const canLive = state.is_live && state.armed && !state.halt;

  function executeLive() {
    if (!window.confirm(
      "Place REAL market orders for today's phase on every live + armed account now?\n\n"
      + "This sends live orders to Robinhood and cannot be undone."
    )) return;
    void act("live", () => api.adminRun("execute", false), "Execute (live)");
  }

  function closeVote() {
    if (!window.confirm("Close today's ballot and lock the consensus basket now?")) return;
    void act("close", () => api.adminRun("close"), "Close vote");
  }

  return (
    <div
      className="glass glass-glow p-4 sm:p-5"
      style={state.will_place_next
        ? { borderColor: "rgba(52, 227, 168, 0.4)", background: "rgba(52, 227, 168, 0.06)" }
        : { borderColor: "rgba(255, 107, 107, 0.4)", background: "rgba(255, 107, 107, 0.05)" }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className={`font-display text-[17px] font-bold sm:text-[19px] ${state.will_place_next ? "text-green" : "text-red"}`}>
          {state.will_place_next
            ? "Next run WILL place orders"
            : "Next run will NOT place orders"}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Pill tone={state.is_live ? "teal" : "neutral"}>
            mode <b>{state.execution_mode}</b>
          </Pill>
          <Pill tone={state.armed ? "ok" : "warn"}>{state.armed ? "armed" : "disarmed"}</Pill>
          {state.halt && <Pill tone="danger">HALT</Pill>}
          {cadence && <Pill tone="neutral">cadence <b>{cadence}</b></Pill>}
        </div>
      </div>

      {!state.will_place_next && state.reasons?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {state.reasons.map((r, i) => (
            <Pill key={i} tone="danger">{r}</Pill>
          ))}
        </div>
      )}

      <ArmControl armed={state.armed} isLive={state.is_live} onChanged={onChanged} />

      {/* manual triggers — the same levers the forge page had. Dry-run is safe
          by construction (run/execute?dry_run=true simulates); the live
          execute stays disabled until the mode+arm+halt gate is actually
          open, so the button can't promise what the executor would refuse. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
        <Button variant="glass" busy={busy === "close"} className="!px-3.5 !py-2 !text-[12.5px]"
          onClick={closeVote}>Close vote</Button>
        <Button variant="glass" busy={busy === "dry"} className="!px-3.5 !py-2 !text-[12.5px]"
          onClick={() => act("dry", () => api.adminRun("execute", true), "Dry-run")}>Dry-run</Button>
        <Button variant="danger" busy={busy === "live"} disabled={!canLive} className="!px-3.5 !py-2 !text-[12.5px]"
          onClick={executeLive}>Execute now (live)</Button>
        <span className="mx-1 hidden h-5 w-px bg-white/10 sm:block" aria-hidden />
        <Button variant="danger" busy={busy === "kill"} className="!px-3.5 !py-2 !text-[12.5px]"
          onClick={() => act("kill", () => api.adminKill(), "Kill")}>Kill</Button>
      </div>

      {result && <div className="mt-3 text-[12.5px] text-muted tnum">{result}</div>}
      {err && <div className="mt-3 text-[12.5px] text-red">⚠ {err}</div>}
    </div>
  );
}
