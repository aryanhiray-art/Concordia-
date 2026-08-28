"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Card, Freshness, Toggle } from "./ui";
import { ClubPerformance } from "./ClubPerformance";

function money(n: number | null | undefined, dp = 0) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: dp });
}

export function AllocationCard({ onChange }: { onChange?: () => void }) {
  // Snapshot from the auth context — no broker read on every Account visit.
  const {
    account: acc, applyAccount, member,
    accountAsOf, accountStale, accountRefreshing, refreshAccount,
  } = useAuth();
  const [compound, setCompound] = useState(Number(member?.settings?.compound ?? 1) === 1);

  async function toggleCompound(on: boolean) {
    setCompound(on);
    try { await api.setCompound(on); onChange?.(); }
    catch { setCompound(!on); } // revert on failure (e.g. locked mid-cycle)
  }
  const [type, setType] = useState<"percent" | "dollar">("percent");
  // The raw text of the box, not a number: "" is the only thing that tells
  // "hasn't picked an amount yet" apart from "deliberately committed 0", and a
  // numeric state also makes the field unclearable — backspacing the last digit
  // gives a NaN valueAsNumber that snaps straight back to 0.
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seeded = useRef(false);

  // Seed the editor from the snapshot ONCE. The 5-minute poll must never yank a
  // half-typed number out from under the member.
  useEffect(() => {
    if (!acc || seeded.current) return;
    seeded.current = true;
    setType(acc.allocation.type);
    // alloc_value defaults to 0 for a brand-new member and the payload carries
    // no "unset" flag, so a stored 0 means nothing has been committed — leave
    // the box empty and let the placeholder suggest, rather than showing a
    // stake the member never chose.
    setValue(acc.allocation.value > 0 ? String(acc.allocation.value) : "");
  }, [acc]);

  // null = nothing entered. A number input reports "" for both an empty box and
  // a half-typed value it can't parse, so this covers both.
  const amount = value.trim() === "" ? null : Number(value);

  // Switching unit re-reads the same digits as a different quantity: a saved
  // 10% stake becomes a $10 stake on one tap, shrinking a real position by
  // ~99% with no warning. Clearing lets the mode's placeholder suggest, so a
  // figure can only be committed in the unit it was typed for.
  function switchType(t: "percent" | "dollar") {
    setType(t); setValue(""); setSaved(false); setErr(null);
  }

  async function save() {
    if (amount == null || !Number.isFinite(amount)) {
      setSaved(false);
      setErr("enter a complete amount first — a blank or half-typed box isn't a commitment of $0.");
      return;
    }
    setBusy(true); setSaved(false); setErr(null);
    // PUT /me/allocation hands back the whole fresh account payload — push it
    // into the context instead of re-fetching it.
    try { applyAccount(await api.setAllocation(type, amount)); setSaved(true); onChange?.(); }
    catch (e: any) { setErr(e?.message || "couldn't save your stake — please try again."); }
    finally { setBusy(false); }
  }

  if (!acc) return null;
  const bal = acc.allocation.balance;
  const resolved = acc.allocation.resolved_usd;
  // The suggestion is the SAME stake in either unit — a tenth of the agentic
  // account — so flipping the toggle never quietly proposes a different amount
  // ($1,000 is a starter stake to one member and the whole account to another).
  // Falls back to a flat figure when the balance snapshot didn't come through,
  // and never suggests 0 on a tiny account.
  const tenth = bal?.total_value ? Math.max(1, Math.round(bal.total_value / 10)) : null;
  const hint = type === "percent" ? "10" : String(tenth ?? 1000);
  // Whether a stake EXISTS is the saved setting (DB), never resolved_usd:
  // resolved_usd is priced off the balance snapshot (alloc.rebalance_budget
  // treats a missing total_value as 0), and member_balance() swallows every
  // broker error into None — so a Robinhood blip stores an ok=True snapshot with
  // balance:null and resolved_usd:0 while alloc_value is untouched. Reading
  // "committed?" off resolved_usd tells a member with a real stake that the club
  // won't trade their account, which is false. Note the SAVED unit, not `type`:
  // `type` is the editor toggle and may already have been flipped unsaved.
  const savedType = acc.allocation.type;
  const savedValue = acc.allocation.value;
  const savedLabel = savedType === "percent" ? `${savedValue}%` : money(savedValue, 2);
  const pnl = acc.pnl;
  const power = acc.power;
  const up = (power.delta ?? 0) >= 0;

  return (
    <Card>
      <div className="flex items-start justify-between">
        <h2 className="label">Your stake in the club</h2>
        {/* pill-plain: a phrase carrying a dollar figure, not a status token. Mono
            uppercase set "$1,000 buying power" as "$1,000 BUYING POWER", which
            gave the number the same weight and rhythm as the words and made a
            dotted mono zero read as an 8. */}
        <span className="pill pill-teal pill-plain">
          {bal ? <><b>{money(bal.buying_power)}</b> buying power</> : "—"}
        </span>
      </div>

      {bal && (
        <div className="mt-2 text-[12.5px] text-subtle">
          Agentic account <span className="font-display text-ink">{money(bal.total_value, 2)}</span>
          {" · "}{money(bal.cash)} cash
        </div>
      )}
      {/* these balances are a cached snapshot, not a live quote — date them */}
      <Freshness asOf={accountAsOf} stale={accountStale} busy={accountRefreshing}
        onRefresh={refreshAccount} className="mt-1.5" />

      {/* allocation editor */}
      <div className="mt-4 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
        <div className="label mb-2.5">How much do you commit?</div>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="inline-flex overflow-hidden rounded-lg border border-white/10">
            {/* switching units is an UNSAVED change — a lingering "✓ saved" next
                to a figure that now means something else is a lie */}
            <button onClick={() => switchType("percent")}
              className={`px-3 py-1.5 text-[13px] ${type === "percent" ? "bg-teal-deep text-black font-semibold" : "text-dim"}`}>%</button>
            <button onClick={() => switchType("dollar")}
              className={`px-3 py-1.5 text-[13px] ${type === "dollar" ? "bg-teal-deep text-black font-semibold" : "text-dim"}`}>$</button>
          </div>
          <div className="flex items-center gap-1.5">
            {type === "dollar" && <span className="text-dim">$</span>}
            <input type="number" min={0} max={type === "percent" ? 100 : undefined}
              value={value} placeholder={hint}
              onChange={(e) => { setValue(e.target.value); setSaved(false); setErr(null); }}
              className="input !w-28 !py-1.5 text-right tnum" />
            {type === "percent" && <span className="text-dim">%</span>}
          </div>
          <Button variant="ok" onClick={save} busy={busy}>Commit</Button>
          {saved && <span className="text-[12px] text-green">✓ saved</span>}
        </div>
        {err && <div className="mt-2.5 text-[12px] text-red">⚠ {err}</div>}
        {/* the SAVED stake, not what's in the box — "Committing $0.00" on a
            member who has never committed reads as a setting they chose */}
        <div className="mt-2.5 text-[12.5px] text-muted">
          {savedValue <= 0
            ? "Nothing committed — the club won't trade your account."
            : resolved > 0
              ? <>Committing <b className="text-teal">{money(resolved, 2)}</b></>
              : <>Committing <b className="text-teal">{savedLabel}</b>
                  {bal
                    ? " — nothing to deploy until the account is funded."
                    : " — couldn't price it right now; your stake is unchanged."}</>}
        </div>
        <div className="mt-3 flex items-center justify-between gap-4 border-t border-white/10 pt-3">
          <div>
            <div className="font-display text-[13px] font-semibold">Compounding</div>
            <div className="mt-0.5 text-[11.5px] leading-snug text-subtle">
              {compound
                ? "your slice rides its own gains and losses from what you committed"
                : "pinned: gains are skimmed to cash, losses topped back up to your committed amount"}
            </div>
          </div>
          <Toggle on={compound} onChange={toggleCompound} />
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
          {/* "Open", not "Club profit": broker cost basis RESETS every rebalance,
              so this is unrealized P&L on the CURRENT positions only — the owner
              read the old label as his lifetime club return (it isn't; the
              account chart on the Overview is the real record). */}
          <div className="label">Open P&amp;L</div>
          {pnl.invested > 0 ? (
            <>
              <div className={`mt-1 font-display text-[22px] font-bold tnum ${pnl.profit >= 0 ? "text-green" : "text-red"}`}>
                {pnl.profit >= 0 ? "+" : ""}{money(pnl.profit, 2)}
              </div>
              <div className="text-[11.5px] text-subtle">
                {money(pnl.value, 2)} value · {money(pnl.invested, 2)} in
                {pnl.profit_pct != null && <span className={pnl.profit >= 0 ? "text-green" : "text-red"}> · {pnl.profit_pct >= 0 ? "+" : ""}{pnl.profit_pct}%</span>}
                <span> · current positions</span>
              </div>
            </>
          ) : (
            <div className="mt-1 text-[12.5px] text-subtle">Nothing deployed right now.</div>
          )}
        </div>
      </div>

      {/* club decisions vs SPY — club-level series, identical for every member */}
      <div className="mt-3 rounded-[12px] border border-white/10 bg-white/[0.02] px-4 py-3">
        <div className="label">Club decisions vs S&amp;P 500</div>
        <div className="mt-2.5">
          <ClubPerformance />
        </div>
      </div>
    </Card>
  );
}
