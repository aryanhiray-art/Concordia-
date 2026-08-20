"use client";

// The console's verdict + every ops control. The headline answers the one
// question this host exists for — "will the next 09:31 ET run place orders,
// and if not, why?" — and the single controls row underneath carries the
// levers that change the answer. Arming demands a typed ARM confirmation (the
// human step that lets real money move); a live execute demands a spelled-out
// confirm; Kill and Disarm are one click — stopping must always be easier
// than starting.

import { useState } from "react";
import { api, type AdminRunResult } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Button, Pill } from "@/components/ui";

// ── arming — typed confirmation, no shortcuts ───────────────────────────────
// Renders INTO the shared controls row: a one-click Disarm when armed, the
// Arm trading… trigger otherwise. The typed-ARM confirm expands as a w-full
// strip inside the same flex-wrap row (its own line, nothing else moves far).
function ArmControl({ armed, onChanged }: { armed: boolean; onChanged: () => void }) {
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
      <>
        <Button variant="danger" busy={busy} onClick={() => setArmed(false)}>Disarm</Button>
        {err && <span className="text-[12px] text-red">⚠ {err}</span>}
      </>
    );
  }

  if (!confirming) {
    return (
      <>
        <Button variant="ok" onClick={() => { setConfirming(true); setErr(null); }}>
          Arm trading…
        </Button>
        {err && <span className="text-[12px] text-red">⚠ {err}</span>}
      </>
    );
  }

  return (
    <>
      <div className="flex w-full flex-wrap items-center gap-2 rounded-[10px] border border-green/25 bg-green/[0.05] px-2.5 py-1.5">
        <span className="text-[11.5px] text-muted">
          arming lets the next run place <b className="text-ink">real orders in every
          connected account</b> — type <b className="text-ink">ARM</b>
        </span>
        <input
          value={typed}
          onChange={(e) => { setTyped(e.target.value); setErr(null); }}
          placeholder="ARM"
          className="input h-8 !w-24 !rounded-[9px] !py-0 text-center font-mono !text-[12px]"
          spellCheck={false} autoComplete="off"
        />
        <Button variant="ok" busy={busy}
          disabled={typed.trim().toUpperCase() !== "ARM"}
          onClick={() => setArmed(true)}>
          Arm
        </Button>
        <button onClick={() => { setConfirming(false); setTyped(""); setErr(null); }}
          className="text-[12px] text-subtle hover:text-ink">cancel</button>
      </div>
      {err && <span className="w-full text-[12px] text-red">⚠ {err}</span>}
    </>
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
      className="glass glass-glow p-4"
      style={state.will_place_next
        ? { borderColor: "rgba(52, 227, 168, 0.4)", background: "rgba(52, 227, 168, 0.06)" }
        : { borderColor: "rgba(255, 107, 107, 0.4)", background: "rgba(255, 107, 107, 0.05)" }}
    >
      {/* verdict + state chips */}
      <div className="flex flex-wrap items-center gap-2.5">
        <div className={`font-display text-[15px] font-bold sm:text-[16px] ${state.will_place_next ? "text-green" : "text-red"}`}>
          {state.will_place_next
            ? "Next run WILL place orders"
            : "Next run will NOT place orders"}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Pill tone={state.is_live ? "teal" : "neutral"}>
            mode <b>{state.execution_mode}</b>
          </Pill>
          <Pill tone={state.armed ? "ok" : "warn"}>{state.armed ? "armed" : "disarmed"}</Pill>
          {state.halt && <Pill tone="danger">HALT</Pill>}
          {cadence && <Pill tone="neutral">cadence <b>{cadence}</b></Pill>}
        </div>
      </div>

      {!state.will_place_next && state.reasons?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {state.reasons.map((r, i) => (
            <Pill key={i} tone="danger">{r}</Pill>
          ))}
        </div>
      )}

      {/* ONE controls row: arm state first, then the manual triggers. Dry-run
          is safe by construction (run/execute?dry_run=true simulates); the
          live execute stays disabled until the mode+arm+halt gate is actually
          open, so the button can't promise what the executor would refuse. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
        <ArmControl armed={state.armed} onChanged={onChanged} />
        <span className="mx-1 hidden h-4 w-px bg-white/10 sm:block" aria-hidden />
        <Button variant="glass" busy={busy === "close"} onClick={closeVote}>Close vote</Button>
        <Button variant="glass" busy={busy === "dry"}
          onClick={() => act("dry", () => api.adminRun("execute", true), "Dry-run")}>Dry-run</Button>
        <Button variant="danger" busy={busy === "live"} disabled={!canLive}
          onClick={executeLive}>Execute live</Button>
        <span className="mx-1 hidden h-4 w-px bg-white/10 sm:block" aria-hidden />
        <Button variant="danger" busy={busy === "kill"}
          onClick={() => act("kill", () => api.adminKill(), "Kill")}>Kill</Button>
      </div>

      {result && <div className="mt-2.5 text-[12px] text-muted tnum">{result}</div>}
      {err && <div className="mt-2.5 text-[12px] text-red">⚠ {err}</div>}
    </div>
  );
}
