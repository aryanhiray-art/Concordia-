"use client";

// /stocks — research. Search the votable universe (plus direct symbol lookups
// the broker can price) and open the FULL analytics page at /stocks/[symbol].
//
// Deliberately NOT the slide-over: inside research a result is a destination
// (deep-linkable, back-button-able, room for the real analytics). The panel
// stays the surface for symbols met elsewhere in the app — a history chip, a
// tally row — where you want a look without losing your place. Both read the
// same GET /api/stocks/{sym}.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { StockSearchResult } from "@/lib/types";
import { Card, Dots } from "@/components/ui";

const SYMBOL_RE = /^[A-Za-z.]{1,10}$/;

export default function StocksPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);   // a query has settled at least once
  const [active, setActive] = useState(0);

  // debounced universe search
  useEffect(() => {
    const query = q.trim();
    if (!query) { setResults([]); setSearching(false); setSearched(false); return; }
    setSearching(true);
    let alive = true;
    const id = setTimeout(async () => {
      try {
        const r = await api.stockSearch(query, 30);
        if (alive) { setResults(r.results); setActive(0); }
      } catch {
        if (alive) setResults([]);
      } finally {
        if (alive) { setSearching(false); setSearched(true); }
      }
    }, 200);
    return () => { alive = false; clearTimeout(id); };
  }, [q]);

  // A typed symbol that ISN'T a universe hit can still be a real stock — the
  // detail endpoint prices anything the broker knows. Offer it as a final row.
  const query = q.trim();
  const direct = SYMBOL_RE.test(query) &&
    !results.some((r) => r.symbol === query.toUpperCase())
    ? query.toUpperCase() : null;
  const rowCount = results.length + (direct ? 1 : 0);

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, rowCount - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && rowCount > 0) {
      e.preventDefault();
      const i = Math.min(active, rowCount - 1);
      const sym = i < results.length ? results[i].symbol : direct;
      if (sym) router.push(`/stocks/${sym}`);
    }
  }

  const row = "flex w-full items-center gap-3.5 rounded-[12px] px-3 py-3 text-left transition";

  return (
    <div className="stagger space-y-5">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">Stocks</h1>
      </header>

      <Card>
        <div className="relative">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2"
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-subtle">
            <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
          </svg>
          <input autoFocus value={q} spellCheck={false}
            onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
            placeholder="Search by symbol or company name…"
            role="combobox" aria-expanded={rowCount > 0} aria-controls="stock-results"
            aria-label="Search stocks"
            className="input !py-3.5 !pl-11 !text-[15px]" />
          {searching && <span className="absolute right-4 top-1/2 -translate-y-1/2"><Dots /></span>}
        </div>

        <div id="stock-results" role="listbox" aria-label="Search results" className="mt-2">
          {query && results.map((r, i) => (
            <Link key={r.symbol} href={`/stocks/${r.symbol}`} role="option" aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              className={`${row} ${i === active ? "bg-white/[0.06]" : ""}`}>
              <span className="grid h-10 w-[56px] flex-none place-items-center rounded-[11px] border border-hair bg-white/[0.04] font-display text-[12px] font-semibold text-muted">
                {r.symbol}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{r.name}</span>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" className="flex-none text-subtle">
                <path d="m9 18 6-6-6-6" />
              </svg>
            </Link>
          ))}

          {direct && searched && !searching && (
            <Link href={`/stocks/${direct}`} role="option" aria-selected={active === results.length}
              onMouseEnter={() => setActive(results.length)}
              className={`${row} ${active === results.length ? "bg-white/[0.06]" : ""}`}>
              <span className="grid h-10 w-[56px] flex-none place-items-center rounded-[11px] border border-teal/30 bg-teal/[0.07] font-display text-[12px] font-semibold text-teal">
                {direct}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13px] text-muted">direct lookup</span>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" className="flex-none text-subtle">
                <path d="m9 18 6-6-6-6" />
              </svg>
            </Link>
          )}

          {query && searched && !searching && rowCount === 0 && (
            <p className="px-1 py-5 text-center text-[13px] text-subtle">
              No matches for “{query}”.
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
