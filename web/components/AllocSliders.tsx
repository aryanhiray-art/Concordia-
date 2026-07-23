"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Alloc, SessionView, UniverseItem } from "@/lib/types";
import { Dots } from "./ui";

const MAX_PICKS = 20;

type Row = { symbol: string; name: string; weight: number };

export function AllocSliders({
  session, participating, onVoted,
}: { session: SessionView; participating: boolean; onVoted: (s: SessionView) => void }) {
  const names = useRef<Record<string, string>>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UniverseItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
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

  // debounced S&P 500 search for the add-menu
  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); setSearching(false); return; }
    setSearching(true);
    const id = setTimeout(async () => {
      try {
        const r = await api.searchUniverse(q, 24);
        r.symbols.forEach((s) => { names.current[s.symbol] = s.name; });
        setResults(r.symbols.filter((s) => !rows.some((x) => x.symbol === s.symbol)));
      } finally { setSearching(false); }
    }, 200);
    return () => clearTimeout(id);
  }, [query, rows]);

  const total = useMemo(() => rows.reduce((a, r) => a + r.weight, 0), [rows]);
  const sumExcept = (i: number) => rows.reduce((a, r, k) => (k === i ? a : a + r.weight), 0);

  function setWeight(i: number, raw: number) {
    const maxV = 100 - sumExcept(i);
    const w = Math.max(0, Math.min(Math.round(raw), Math.round(maxV)));
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, weight: w } : r)));
    setSaved(false); setErr(null);
  }
  function remove(i: number) { setRows((rs) => rs.filter((_, k) => k !== i)); setSaved(false); setErr(null); }
  function add(sym: string) {
    if (rows.some((r) => r.symbol === sym) || rows.length >= MAX_PICKS) return;
    setRows((rs) => [...rs, { symbol: sym, name: names.current[sym] || sym, weight: 0 }]);
    setMenuOpen(false); setQuery(""); setResults([]); setSaved(false); setErr(null);
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
      <div className="glass rise p-6 text-center">
        <div className="text-[14px] text-muted">
          Participation is <b className="text-amber">off</b> — turn it on in{" "}
          <a href="/account" className="text-teal">Account</a> to allocate your vote.
        </div>
      </div>
    );
  }

  const full = Math.round(total) >= 100;
  return (
    <div className="glass rise p-5 sm:p-6">
      <span className="glint" aria-hidden />
      <div className="flex items-center justify-between border-b border-hair pb-3">
        <span className="label">Allocate your voting power</span>
        <span className={`font-display text-[13px] ${full ? "text-green" : "text-muted"}`}>
          Total <b className="tnum">{Math.round(total)}%</b> / 100%
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-subtle">
          No picks yet — add names below and slide to set how much of your power backs each.
        </p>
      ) : (
        <div className="mt-1">
          {rows.map((r, i) => (
            <div key={r.symbol} className="vrow">
              <div className="vtk">
                <b>{r.symbol}</b>
                <s>{r.name}</s>
              </div>
              <input className="slider" type="range" min={0} max={100} step={1} value={r.weight}
                onChange={(e) => setWeight(i, e.target.valueAsNumber || 0)} />
              <input className="pctin tnum" type="number" min={0} max={100} value={r.weight}
                onChange={(e) => setWeight(i, e.target.valueAsNumber || 0)} />
              <span className="text-[13px] text-subtle">%</span>
              <button className="del" onClick={() => remove(i)} title="Remove" aria-label="Remove">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* add stock */}
      <div className="relative mt-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <button onClick={() => setMenuOpen((v) => !v)} disabled={rows.length >= MAX_PICKS}
            className="inline-flex items-center gap-2 rounded-[11px] border border-teal/25 bg-teal/[0.08] px-4 py-2.5 text-[13px] font-semibold text-teal transition hover:bg-teal/[0.14] disabled:opacity-50">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
            Add stock
          </button>
          {rows.length > 1 && (
            <button onClick={distributeEven} className="text-[12.5px] text-subtle hover:text-teal">split evenly</button>
          )}
          <div className="ml-auto flex items-center gap-2.5">
            {saved && <span className="text-[12px] text-green">✓ vote saved</span>}
            <button onClick={submit} disabled={saving || rows.length === 0}
              className="btn btn-go !px-5 !py-2.5 text-[13px]">{saving ? "saving…" : "Submit vote"}</button>
          </div>
        </div>
        {err && (
          <div className="mt-2.5 text-right text-[12px] text-red">
            ⚠ Your vote was <b>NOT</b> saved — {err}
          </div>
        )}

        {menuOpen && (
          <div className="enter absolute left-0 top-[52px] z-10 max-h-72 w-[min(320px,90vw)] overflow-y-auto rounded-[12px] border border-white/10 bg-[#11161f] p-1.5 shadow-2xl">
            <div className="flex items-center gap-2 px-2 py-1.5">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" className="text-subtle"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
              <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search the S&P 500…" spellCheck={false}
                className="w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-subtle" />
              {searching && <Dots />}
            </div>
            {results.map((r) => (
              <button key={r.symbol} onClick={() => add(r.symbol)}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-white/[0.05]">
                <b className="font-display text-[13px]">{r.symbol}</b>
                <s className="truncate text-[11.5px] text-subtle no-underline">{r.name}</s>
              </button>
            ))}
            {!searching && query && results.length === 0 && (
              <div className="px-2.5 py-2 text-[12px] text-subtle">No matches.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
