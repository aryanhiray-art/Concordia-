"use client";

// The ballot: search → add → weight → submit, in one card.
//
// The old shape was a slider list with an "Add stock" button that popped a
// second, weaker search menu. Search now lives inline at the top of the card
// (StockSearch, the app's only matcher) and the meter under the header answers
// the question a voter actually has — how much power is still unspent.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Alloc, SessionView } from "@/lib/types";
import { mergePick } from "./AddToBallot";
import { StockLink } from "./StockLink";
import { StockSearch } from "./StockSearch";

/** Mirrors backend settings.max_picks (MAX_PICKS, default 20). */
const MAX_PICKS = 20;

type Row = { symbol: string; name: string; weight: number };

export function AllocSliders({
  session, participating, onVoted,
}: { session: SessionView; participating: boolean; onVoted: (s: SessionView) => void }) {
  const names = useRef<Record<string, string>>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // learn company names from the live tally
  useEffect(() => { session.tally.forEach((t) => { names.current[t.symbol] = t.name; }); }, [session.tally]);

  // initialise rows from my saved allocations once per session
  useEffect(() => {
    setRows((session.my_picks || []).map((a: Alloc) => ({
      symbol: a.symbol, name: names.current[a.symbol] || a.symbol, weight: Math.round(a.weight),
    })));
  }, [session.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = useMemo(() => rows.reduce((a, r) => a + r.weight, 0), [rows]);
  const sumExcept = (i: number) => rows.reduce((a, r, k) => (k === i ? a : a + r.weight), 0);

  function setWeight(i: number, raw: number) {
    const maxV = 100 - sumExcept(i);
    const w = Math.max(0, Math.min(Math.round(raw), Math.round(maxV)));
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, weight: w } : r)));
    setSaved(false); setErr(null);
  }
  function remove(i: number) { setRows((rs) => rs.filter((_, k) => k !== i)); setSaved(false); setErr(null); }

  // Same merge policy as "Add to ballot" on the research surfaces: the new pick
  // takes an equal share out of unspent power, and only if there is none does
  // it scale the standing picks down to fit. Existing picks are never dropped
  // and the total can never exceed 100.
  function add(symbol: string, name: string) {
    if (rows.some((r) => r.symbol === symbol) || rows.length >= MAX_PICKS) return;
    names.current[symbol] = name;
    const { next } = mergePick(rows.map((r) => ({ symbol: r.symbol, weight: r.weight })), symbol);
    setRows(next.map((a) => ({
      symbol: a.symbol, name: names.current[a.symbol] || a.symbol, weight: Math.round(a.weight),
    })));
    setSaved(false); setErr(null);
  }

  function distributeEven() {
    if (!rows.length) return;
    const each = Math.floor(100 / rows.length);
    let rem = 100 - each * rows.length;
    setRows((rs) => rs.map((r) => { const extra = rem-- > 0 ? 1 : 0; return { ...r, weight: each + extra }; }));
    setSaved(false); setErr(null);
  }

  async function submit() {
    setSaving(true); setSaved(false); setErr(null);
    try {
      const s = await api.castVote(rows.map((r) => ({ symbol: r.symbol, weight: r.weight })));
      onVoted(s); setSaved(true);
    } catch (e: any) {
      setErr(e?.message || "something went wrong on the server. Please try again.");
    } finally { setSaving(false); }
  }

  if (!participating) {
    return (
      <div className="glass rise p-5 sm:p-6">
        <span className="label">Your ballot</span>
        <p className="mt-3 text-[13.5px] text-muted">
          Participation is <b className="text-amber">off</b> — turn it on in{" "}
          <Link href="/account" className="font-semibold text-teal hover:underline">Account</Link> to allocate your vote.
        </p>
      </div>
    );
  }

  const left = Math.max(0, 100 - Math.round(total));
  const full = left === 0;
  return (
    <div className="glass rise p-4 sm:p-5">
      <span className="glint" aria-hidden />

      <div className="flex items-baseline justify-between gap-3">
        <span className="label">Your ballot</span>
        <span className="font-display text-[12.5px] tnum">
          {full
            ? <b className="text-green">fully allocated</b>
            : <><b className="text-ink">{left}%</b><span className="font-medium text-subtle"> of your power left</span></>}
        </span>
      </div>
      <div className="bar mt-2.5"><i style={{ width: `${Math.min(100, Math.round(total))}%` }} /></div>

      <div className="mt-3.5">
        <StockSearch onPick={add} picked={rows.map((r) => r.symbol)}
          disabled={rows.length >= MAX_PICKS}
          disabledNote={`Ballot is full — ${MAX_PICKS} picks max`} />
      </div>

      {rows.length === 0 ? (
        <p className="py-7 text-center text-[13px] text-subtle">
          No picks yet — search above to add a name.
        </p>
      ) : (
        <div className="mt-1 max-h-[min(44vh,380px)] overflow-y-auto">
          {rows.map((r, i) => (
            <div key={r.symbol} className="vrow">
              <div className="vtk">
                {/* the symbol opens the stock detail panel; .vtk b keeps its skin */}
                <StockLink symbol={r.symbol} unstyled className="text-left transition hover:text-teal">
                  <b>{r.symbol}</b>
                </StockLink>
                <s>{r.name}</s>
              </div>
              <input className="slider" type="range" min={0} max={100} step={1} value={r.weight}
                aria-label={`${r.symbol} weight`}
                onChange={(e) => setWeight(i, e.target.valueAsNumber || 0)} />
              <input className="pctin tnum" type="number" min={0} max={100} value={r.weight}
                aria-label={`${r.symbol} weight percent`}
                onChange={(e) => setWeight(i, e.target.valueAsNumber || 0)} />
              <span className="text-[13px] text-subtle">%</span>
              <button className="del" onClick={() => remove(i)} title="Remove" aria-label={`Remove ${r.symbol}`}>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3.5 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-hair pt-3.5">
        {rows.length > 1 && (
          <button onClick={distributeEven} className="text-[12.5px] text-subtle transition hover:text-teal">split evenly</button>
        )}
        <div className="ml-auto flex items-center gap-2.5">
          {saved && <span className="text-[12px] text-green">✓ vote saved</span>}
          <button onClick={submit} disabled={saving || rows.length === 0}
            className="btn btn-go !px-5 !py-2.5 text-[13px]">{saving ? "saving…" : "Submit vote"}</button>
        </div>
      </div>
      {err && (
        <div className="mt-2 text-right text-[12px] text-red">
          ⚠ Your vote was <b>NOT</b> saved — {err}
        </div>
      )}
    </div>
  );
}
