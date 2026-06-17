"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Account } from "@/lib/types";
import { Button, Card } from "./ui";

function money(n: number | null | undefined, dp = 0) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: dp });
}

export function AllocationCard({ onChange }: { onChange?: () => void }) {
  const [acc, setAcc] = useState<Account | null>(null);
  const [type, setType] = useState<"percent" | "dollar">("percent");
  const [value, setValue] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      const a = await api.account();
      setAcc(a);
      setType(a.allocation.type);
      setValue(a.allocation.value);
    } catch {}
  }
  useEffect(() => { load(); }, []);

  async function save() {
    setBusy(true); setSaved(false);
    try { const a = await api.setAllocation(type, Number(value)); setAcc(a); setSaved(true); onChange?.(); }
    finally { setBusy(false); }
  }

  if (!acc) return null;
  const bal = acc.allocation.balance;
  const resolved = acc.allocation.resolved_usd;
  const pnl = acc.pnl;
  const power = acc.power;
  const up = (power.delta ?? 0) >= 0;

  return (
    <Card>
      <div className="flex items-start justify-between">
        <h2 className="label">Your stake in the club</h2>
        <span className="pill pill-teal">{bal ? money(bal.buying_power) + " buying power" : "—"}</span>
      </div>

      {bal && (
        <div className="mt-2 text-[12.5px] text-subtle">
          Agentic account <span className="font-display text-ink">{money(bal.total_value)}</span>
          {" · "}{money(bal.cash)} cash
        </div>
      )}

      {/* allocation editor */}
      <div className="mt-4 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
        <div className="label mb-2.5">How much do you commit?</div>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="inline-flex overflow-hidden rounded-lg border border-white/10">
            <button onClick={() => setType("percent")}
              className={`px-3 py-1.5 text-[13px] ${type === "percent" ? "bg-teal-deep text-black font-semibold" : "text-dim"}`}>%</button>
            <button onClick={() => setType("dollar")}
              className={`px-3 py-1.5 text-[13px] ${type === "dollar" ? "bg-teal-deep text-black font-semibold" : "text-dim"}`}>$</button>
          </div>
          <div className="flex items-center gap-1.5">
            {type === "dollar" && <span className="text-dim">$</span>}
            <input type="number" min={0} max={type === "percent" ? 100 : undefined} value={value}
              onChange={(e) => { setValue(e.target.valueAsNumber || 0); setSaved(false); }}
              className="input !w-28 !py-1.5 text-right tnum" />
            {type === "percent" && <span className="text-dim">%</span>}
          </div>
          <Button variant="ok" onClick={save} busy={busy}>Commit</Button>
          {saved && <span className="text-[12px] text-green">✓ saved</span>}
        </div>
        <div className="mt-2.5 text-[12.5px] text-muted">
          Committing <b className="text-teal">{money(resolved, 2)}</b> — only this is traded + tracked. The rest of your account is untouched.
        </div>
      </div>

      {/* voting power + profit */}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-[12px] border border-white/10 bg-white/[0.02] px-4 py-3">
          <div className="label">Voting power</div>
          <div className="mt-1 font-display text-[22px] font-bold tnum">{money(power.power)}</div>
          <div className="text-[11.5px] text-subtle">
            {power.delta != null && power.delta !== 0
              ? <span className={up ? "text-green" : "text-red"}>{up ? "▲" : "▼"} {money(Math.abs(power.delta))} this week</span>
              : "your stake = your say in the basket"}
          </div>
        </div>
        <div className="rounded-[12px] border border-white/10 bg-white/[0.02] px-4 py-3">
          <div className="label">Club profit</div>
          {pnl.invested > 0 ? (
            <>
              <div className={`mt-1 font-display text-[22px] font-bold tnum ${pnl.profit >= 0 ? "text-green" : "text-red"}`}>
                {pnl.profit >= 0 ? "+" : ""}{money(pnl.profit, 2)}
              </div>
              <div className="text-[11.5px] text-subtle">
                {money(pnl.value, 2)} value · {money(pnl.invested, 2)} in
                {pnl.profit_pct != null && <span className={pnl.profit >= 0 ? "text-green" : "text-red"}> · {pnl.profit_pct >= 0 ? "+" : ""}{pnl.profit_pct}%</span>}
              </div>
            </>
          ) : (
            <div className="mt-1 text-[12.5px] text-subtle">No club trades yet — profit appears once the basket runs live.</div>
          )}
        </div>
      </div>
    </Card>
  );
}
