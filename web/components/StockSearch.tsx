"use client";

// The ONE stock search in the app. It used to be a whole tab (/stocks) plus a
// second, weaker matcher hidden behind "Add stock" on the ballot; both are gone
// and this is what replaced them, living inside the voting card where picking a
// name actually does something.
//
// Three kinds of row, one keyboard model (↑/↓/Enter/Esc):
//   • a votable name          → Enter ADDS it to the ballot
//   • a name already on it    → Enter opens research (adding again is a no-op)
//   • a typed symbol the universe doesn't have → the direct-lookup row: it can't
//     be voted on, so Enter opens research instead of pretending otherwise
// Every row also carries a small research button, so a member can read a name
// before spending power on it without leaving the ballot.
//
// Empty box = browse the whole votable universe (the old /stocks landing view).
// That list is fetched once per mount, on first focus — never on page load.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { StockSearchResult } from "@/lib/types";
import { useStockPanel } from "@/components/StockPanel";
import { Dots } from "@/components/ui";

// What a US ticker can actually look like (≤5 letters, optional class suffix:
// BRK.B). The old /stocks page allowed 10, which made "microsoft" render as a
// ticker chip on the direct-lookup row.
const SYMBOL_RE = /^[A-Za-z]{1,5}(\.[A-Za-z]{1,2})?$/;

export function StockSearch({
  onPick, picked = [], disabled = false, disabledNote,
}: {
  /** add a votable name to the ballot */
  onPick: (symbol: string, name: string) => void;
  /** symbols already on the ballot — shown, but not addable twice */
  picked?: string[];
  disabled?: boolean;
  disabledNote?: string;
}) {
  const { openStock } = useStockPanel();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [settled, setSettled] = useState(false);   // a typed query has landed
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const universe = useRef<StockSearchResult[] | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const query = q.trim();

  // debounced matcher; the empty-query universe is fetched once and remembered
  useEffect(() => {
    if (!open) return;
    let alive = true;
    if (!query) {
      if (universe.current) {
        setResults(universe.current); setActive(0); setSettled(false); setSearching(false);
        return;
      }
      setSearching(true);
      api.stockSearch("", 500)
        .then((r) => { if (alive) { universe.current = r.results; setResults(r.results); setActive(0); } })
        .catch(() => { if (alive) setResults([]); })
        .finally(() => { if (alive) { setSearching(false); setSettled(false); } });
      return () => { alive = false; };
    }
    setSearching(true);
    const id = setTimeout(async () => {
      try {
        const r = await api.stockSearch(query, 30);
        if (alive) { setResults(r.results); setActive(0); }
      } catch {
        if (alive) setResults([]);
      } finally {
        if (alive) { setSearching(false); setSettled(true); }
      }
    }, 200);
    return () => { alive = false; clearTimeout(id); };
  }, [query, open]);

  // click anywhere else = done searching
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // A typed symbol the universe doesn't carry can still be a real stock the
  // broker prices — offer it as research, never as a vote.
  const direct = SYMBOL_RE.test(query) && !results.some((r) => r.symbol === query.toUpperCase())
    ? query.toUpperCase() : null;
  const rowCount = results.length + (direct && settled && !searching ? 1 : 0);

  function research(sym: string) { setOpen(false); openStock(sym); }

  function activate(i: number) {
    if (i < results.length) {
      const r = results[i];
      if (picked.includes(r.symbol)) { research(r.symbol); return; }
      onPick(r.symbol, r.name);
      setQ(""); setOpen(false);
      return;
    }
    if (direct) research(direct);
  }

  function move(delta: number) {
    setOpen(true);
    setActive((a) => {
      const next = Math.max(0, Math.min(a + delta, rowCount - 1));
      listRef.current?.children[next]?.scrollIntoView({ block: "nearest" });
      return next;
    });
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Escape") { setOpen(false); }
    else if (e.key === "Enter" && open && rowCount > 0) {
      e.preventDefault();
      activate(Math.min(active, rowCount - 1));
    }
  }

  const row = "flex w-full min-w-0 items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-left transition";

  return (
    <div ref={boxRef} className="relative z-20">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
        className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-subtle">
        <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
      </svg>
      <input value={q} spellCheck={false} disabled={disabled}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onKeyDown={onKey}
        placeholder={disabled ? disabledNote || "Ballot is full" : "Add a name — symbol or company"}
        role="combobox" aria-expanded={open && rowCount > 0} aria-controls="ballot-search-results"
        aria-label="Search stocks to add to your ballot"
        className="input !py-2.5 !pl-10 !text-[13.5px] disabled:opacity-60" />
      {searching && <span className="absolute right-3.5 top-1/2 -translate-y-1/2"><Dots /></span>}

      {open && !disabled && (
        <div className="enter absolute left-0 right-0 top-[46px] z-30 overflow-hidden rounded-[13px] border border-white/10 bg-[#11161f] p-1.5 shadow-2xl">
          {!query && results.length > 0 && (
            <div className="label px-2 pb-1.5 pt-1 !text-[10px]">All votable names · {results.length}</div>
          )}
          <div ref={listRef} id="ballot-search-results" role="listbox" aria-label="Search results"
            className="max-h-[248px] overflow-y-auto">
            {results.map((r, i) => {
              const on = picked.includes(r.symbol);
              return (
                <div key={r.symbol} className={`${row} ${i === active ? "bg-white/[0.06]" : ""}`}
                  onMouseEnter={() => setActive(i)}>
                  <button type="button" role="option" aria-selected={i === active}
                    onClick={() => activate(i)}
                    className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                    <span className={`grid h-7 w-[52px] flex-none place-items-center rounded-[9px] border font-display text-[11.5px] font-semibold
                      ${on ? "border-green/30 bg-green/[0.08] text-green" : "border-hair bg-white/[0.04] text-muted"}`}>
                      {r.symbol}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px]">{r.name}</span>
                    <span className="flex-none text-[11px] text-subtle">{on ? "on ballot" : "add"}</span>
                  </button>
                  <button type="button" onClick={() => research(r.symbol)} title={`Research ${r.symbol}`}
                    aria-label={`Research ${r.symbol}`}
                    className="grid h-7 w-7 flex-none place-items-center rounded-[8px] text-subtle transition hover:bg-white/[0.06] hover:text-teal">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M3 17l5-6 4 4 6-8" /><path d="M14 7h4v4" />
                    </svg>
                  </button>
                </div>
              );
            })}

            {direct && settled && !searching && (
              <button key={direct} type="button" role="option" aria-selected={active === results.length}
                onMouseEnter={() => setActive(results.length)} onClick={() => activate(results.length)}
                className={`${row} ${active === results.length ? "bg-white/[0.06]" : ""}`}>
                <span className="grid h-7 w-[52px] flex-none place-items-center overflow-hidden rounded-[9px] border border-teal/30 bg-teal/[0.07] font-display text-[11.5px] font-semibold text-teal">
                  {direct}
                </span>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted">not votable — open research</span>
              </button>
            )}
          </div>

          {query && settled && !searching && rowCount === 0 && (
            <div className="px-2.5 py-3 text-center text-[12.5px] text-subtle">No matches for “{query}”.</div>
          )}
        </div>
      )}
    </div>
  );
}
