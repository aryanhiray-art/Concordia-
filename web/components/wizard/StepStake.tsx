"use client";

// Step 5 — "Commit your stake": the wizard's compact allocation editor (the
// account page has the richer AllocationCard; the %/$ + setAllocation logic is
// mirrored, not shared). The committed slice is all the club ever trades and
// becomes voting power. Continue stays disabled until a value > 0 is saved.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Dots } from "@/components/ui";
import { money } from "./money";

export function StepStake({ onNext }: { onNext: () => void }) {
  // Balance comes from the shared snapshot (fetched once when the member — or
  // their brand-new Robinhood link — was established). `reloadAccount` is the
  // cheap cached re-read behind "try again".
  const { account: acc, accountReady, accountError, applyAccount, reloadAccount } = useAuth();
  const [type, setType] = useState<"percent" | "dollar">("percent");
  const [value, setValue] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seeded = useRef(false);

  // seed the editor once — a background refresh must not overwrite what the
  // member is typing
  useEffect(() => {
    if (!acc || seeded.current) return;
    seeded.current = true;
    setType(acc.allocation.type);
    setValue(acc.allocation.value);
  }, [acc]);

  async function save() {
    setBusy(true); setSaved(false); setErr(null);
    // the PUT returns the fresh account — hand it to the context so StepFinish's
    // recap (and the Account page) see it without another round-trip
    try { applyAccount(await api.setAllocation(type, Number(value))); setSaved(true); }
    catch (e: any) { setErr(e?.message || "couldn't save your stake — please try again"); }
    finally { setBusy(false); }
  }

  if (!acc) {
    return accountReady ? (
      <div className="py-4 text-[13px] text-muted">
        ⚠ Couldn&apos;t load your account balance.{accountError ? ` (${accountError})` : ""}{" "}
        <button onClick={reloadAccount} className="text-teal underline underline-offset-2">try again</button>
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
        Choose how much of your account the club trades. That amount becomes
        your <b className="text-ink">voting power</b> —{" "}
        <b className="text-ink">the club never touches the rest</b>. A per-cycle
        safety cap applies on top.
      </p>

      {bal && (
        <div className="mt-3 text-[12.5px] text-subtle">
          Agentic account <span className="font-display font-semibold text-ink">{money(bal.total_value, 2)}</span>
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
          the rest of your account stays untouched.
        </div>
      </div>

      <Button variant="go" className="mt-4 w-full" onClick={onNext} disabled={!committed}>
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
