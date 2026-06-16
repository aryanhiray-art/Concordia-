"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { SessionView, UniverseItem } from "@/lib/types";
import { AllocRow, Card, Chip, Countdown, Dots } from "./ui";

const MAX_PICKS = 20;

export function Ballot({
  session, participating, onVoted,
}: {
  session: SessionView; participating: boolean; onVoted: (s: SessionView) => void;
}) {
  const [picks, setPicks] = useState<string[]>(session.my_picks);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UniverseItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const names = useRef<Record<string, string>>({});

  useEffect(() => { setPicks(session.my_picks); }, [session.id]); // eslint-disable-line
  // learn names from the live tally so picked chips can show them
  useEffect(() => { session.tally.forEach((t) => { names.current[t.symbol] = t.name; }); }, [session.tally]);

  // debounced S&P 500 search
  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); setSearching(false); return; }
    setSearching(true);
    const id = setTimeout(async () => {
      try {
        const r = await api.searchUniverse(q, 30);
        r.symbols.forEach((s) => { names.current[s.symbol] = s.name; });
        setResults(r.symbols);
      } finally { setSearching(false); }
    }, 220);
    return () => clearTimeout(id);
  }, [query]);

  async function save(next: string[]) {
    setPicks(next);
    setSaving(true);
    try { onVoted(await api.castVote(next)); } finally { setSaving(false); }
  }
  const add = (sym: string) => { if (!picks.includes(sym) && picks.length < MAX_PICKS) save([...picks, sym]); };
  const remove = (sym: string) => save(picks.filter((s) => s !== sym));

  const voteMap = useMemo(() => {
    const m = new Map<string, number>();
    session.tally.forEach((t) => m.set(t.symbol, t.votes));
    return m;
  }, [session.tally]);
  const maxVotes = Math.max(1, ...session.tally.map((t) => t.votes));
  const basket = session.tally.slice(0, session.top_n); // forming top-N basket
  const open = session.status === "open";
  const locked = !participating || !open;

  return (
    <Card className="enter">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="label">Today&apos;s ballot · {session.trade_date}</h2>
          <p className="mt-1.5 text-[12.5px] text-subtle">
            Search the S&amp;P 500 and add up to <b className="text-teal">{MAX_PICKS}</b>.
            The top {session.top_n} by votes become the basket.
          </p>
        </div>
        <div className="text-right">
          {open ? (
            <>
              <div className="label !text-[10px]">closes in</div>
              <div className="font-display text-[15px] font-bold text-teal"><Countdown to={session.closes_at} /></div>
            </>
          ) : <span className="text-[13px] text-red">voting closed</span>}
          <div className="mt-0.5 text-[11.5px] text-subtle">{session.voters} voter{session.voters === 1 ? "" : "s"}</div>
        </div>
      </div>

      {!participating ? (
        <div className="mt-5 rounded-[14px] border border-white/10 bg-white/[0.02] px-4 py-6 text-center text-[13px] text-muted">
          Participation is <b className="text-amber">off</b> — flip the switch in <b>Your account</b> to vote.
        </div>
      ) : (
        <>
          {/* search + add */}
          <div className="mt-5">
            <div className="flex items-center gap-2.5 rounded-[12px] border border-white/10 bg-white/[0.04] px-3.5 py-2.5 focus-within:border-teal/50">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
                className="text-subtle" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
              <input value={query} onChange={(e) => setQuery(e.target.value)} disabled={picks.length >= MAX_PICKS}
                placeholder={picks.length >= MAX_PICKS ? `You've added the max (${MAX_PICKS})` : "Search the S&P 500 — ticker or company"}
                className="w-full bg-transparent text-[14px] text-ink outline-none placeholder:text-subtle disabled:opacity-60"
                spellCheck={false} autoComplete="off" />
              {searching && <Dots />}
            </div>

            {results.length > 0 && (
              <div className="enter mt-2 max-h-64 overflow-y-auto rounded-[12px] border border-white/10 bg-[#0a0d13]/80 backdrop-blur">
                {results.map((r) => {
                  const has = picks.includes(r.symbol);
                  const full = picks.length >= MAX_PICKS;
                  return (
                    <button key={r.symbol} onClick={() => add(r.symbol)} disabled={has || full}
                      className="tile flex w-full items-center justify-between px-3.5 py-2.5 text-left hover:bg-white/[0.04] disabled:opacity-45">
                      <span className="flex items-baseline gap-2.5">
                        <span className="font-display text-[14px] font-bold">{r.symbol}</span>
                        <span className="truncate text-[12px] text-subtle">{r.name}</span>
                      </span>
                      <span className={`text-[12px] ${has ? "text-teal" : "text-subtle"}`}>{has ? "✓ added" : "+ add"}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* my picks */}
          <div className="mt-4">
            <div className="flex items-center justify-between">
              <div className="label">Your picks · {picks.length}/{MAX_PICKS}</div>
              {saving && <span className="text-[11px] text-subtle">saving…</span>}
            </div>
            {picks.length === 0 ? (
              <p className="mt-2 text-[12.5px] text-subtle">No picks yet — search above and add names you want to buy.</p>
            ) : (
              <div className="mt-2.5 flex flex-wrap gap-2">
                {picks.map((sym) => (
                  <Chip key={sym} onRemove={() => remove(sym)}>
                    {sym}
                    {voteMap.get(sym) ? <span className="text-teal/80">·{voteMap.get(sym)}</span> : null}
                  </Chip>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* live consensus leaderboard */}
      {basket.length > 0 && (
        <div className="mt-5 rounded-[14px] border border-teal/15 bg-teal/[0.04] p-4">
          <div className="label !text-teal mb-3">Forming basket · top {session.top_n} by votes</div>
          <div className="flex flex-col gap-2.5">
            {basket.map((t) => (
              <AllocRow key={t.symbol} ticker={t.symbol} votes={t.votes} max={maxVotes} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
