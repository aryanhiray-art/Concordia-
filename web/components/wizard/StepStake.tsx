"use client";

// Step 4 — "Commit your stake": the wizard's compact allocation editor (the
// account page has the richer AllocationCard; the %/$ + setAllocation logic is
// mirrored, not shared). The committed slice is all the club ever trades and
// becomes voting power. Continue stays disabled until a value > 0 is saved.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Account } from "@/lib/types";
import { Button, Dots } from "@/components/ui";
import { money } from "./money";

export function StepStake({ onNext }: { onNext: () => void }) {
  const [acc, setAcc] = useState<Account | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [type, setType] = useState<"percent" | "dollar">("percent");
  const [value, setValue] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadErr(false);
    try {
      const a = await api.account();
      setAcc(a);
      setType(a.allocation.type);
      setValue(a.allocation.value);
    } catch { setLoadErr(true); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save() {
    setBusy(true); setSaved(false); setErr(null);
    try { const a = await api.setAllocation(type, Number(value)); setAcc(a); setSaved(true); }
    catch (e: any) { setErr(e?.message || "couldn't save your stake — please try again"); }
    finally { setBusy(false); }
  }

  if (!acc) {
    return loadErr ? (
      <div className="py-4 text-[13px] text-muted">
        ⚠ Couldn&apos;t load your account balance.{" "}
        <button onClick={load} className="text-teal underline underline-offset-2">try again</button>
      </div>
    ) : (
      <div className="flex items-center gap-3 py-6 text-[13px] text-muted">
        <Dots /> Loading your account…
      </div>
    );
  }

  const bal = acc.allocation.balance;
  const committed = (acc.allocation.resolved_usd ?? 0) > 0;

  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-muted">
        Pick the slice of your account you commit to the club. That slice is{" "}
        <b className="text-ink">all the club ever trades</b> — everything else in your
        account stays untouched — and it becomes your <b className="text-ink">voting power</b>.
        A global per-cycle safety cap applies on top.
      </p>

      {bal && (
        <div className="mt-3 text-[12.5px] text-subtle">
          Agentic account <span className="font-display font-semibold text-ink">{money(bal.total_value)}</span>
          {" · "}{money(bal.cash)} cash{" · "}{money(bal.buying_power)} buying power
        </div>
      )}

      <div className="mt-4 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
        <div className="label mb-2.5">How much do you commit?</div>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="inline-flex overflow-hidden rounded-lg border border-white/10">
            <button onClick={() => { setType("percent"); setSaved(false); setErr(null); }}
              className={`px-3 py-1.5 text-[13px] ${type === "percent" ? "bg-teal-deep font-semibold text-black" : "text-muted"}`}>%</button>
            <button onClick={() => { setType("dollar"); setSaved(false); setErr(null); }}
              className={`px-3 py-1.5 text-[13px] ${type === "dollar" ? "bg-teal-deep font-semibold text-black" : "text-muted"}`}>$</button>
          </div>
          <div className="flex items-center gap-1.5">
            {type === "dollar" && <span className="text-subtle">$</span>}
            <input type="number" min={0} max={type === "percent" ? 100 : undefined} value={value}
              onChange={(e) => { setValue(e.target.valueAsNumber || 0); setSaved(false); setErr(null); }}
              className="input tnum !w-28 !py-1.5 text-right" />
            {type === "percent" && <span className="text-subtle">%</span>}
          </div>
          <Button variant="ok" onClick={save} busy={busy}>Commit</Button>
          {saved && <span className="text-[12px] text-green">✓ saved</span>}
        </div>
        {err && <div className="mt-2.5 text-[12px] text-red">⚠ {err}</div>}
        <div className="mt-2.5 text-[12.5px] text-muted">
          Committing <b className="text-teal">{money(acc.allocation.resolved_usd, 2)}</b> —
          only this is traded + tracked.
        </div>
      </div>

      <Button variant="go" className="mt-5 w-full" onClick={onNext} disabled={!committed}>
        Continue
      </Button>
      {!committed && (
        <p className="mt-2 text-center text-[11.5px] text-subtle">
          Commit an amount above zero to continue.
        </p>
      )}
    </div>
  );
}
