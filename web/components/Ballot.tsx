"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { SessionView, UniverseItem } from "@/lib/types";
import { Button, Card, Countdown } from "./ui";

export function Ballot({
  universe, session, onVoted,
}: {
  universe: UniverseItem[]; session: SessionView; onVoted: (s: SessionView) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(session.my_picks));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { setSelected(new Set(session.my_picks)); }, [session.id]); // eslint-disable-line

  const votes = useMemo(() => {
    const m = new Map<string, number>();
    session.tally.forEach((t) => m.set(t.symbol, t.votes));
    return m;
  }, [session.tally]);
  const maxVotes = Math.max(1, ...session.tally.map((t) => t.votes));
  const open = session.status === "open";

  function toggle(sym: string) {
    if (!open) return;
    setSaved(false);
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });
  }

  async function submit() {
    setBusy(true);
    try { const s = await api.castVote(Array.from(selected)); onVoted(s); setSaved(true); }
    finally { setBusy(false); }
  }

  const dirty = useMemo(() => {
    const a = Array.from(selected).sort().join(",");
    const b = [...session.my_picks].sort().join(",");
    return a !== b;
  }, [selected, session.my_picks]);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="label">Today&apos;s ballot · {session.trade_date}</h2>
          <p className="mt-1.5 text-[12.5px] text-subtle">
            Pick the names to buy. The top <b className="text-teal">{session.top_n}</b> by votes become the basket.
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

      <div className="mt-5 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
        {universe.map((u) => {
          const on = selected.has(u.symbol);
          const v = votes.get(u.symbol) ?? 0;
          return (
            <button key={u.symbol} onClick={() => toggle(u.symbol)} disabled={!open}
              className={`group relative overflow-hidden rounded-[14px] border px-3.5 py-3 text-left transition-all duration-150
                ${on
                  ? "border-teal/60 bg-teal/[0.12] shadow-[0_10px_30px_-16px_rgba(45,212,191,0.7),inset_0_1px_0_rgba(255,255,255,0.08)]"
                  : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:-translate-y-[1px]"}
                ${!open ? "cursor-default" : "cursor-pointer"}`}>
              <span className="absolute inset-x-0 bottom-0 h-[3px]"
                style={{ width: `${(v / maxVotes) * 100}%`, background: "linear-gradient(90deg,var(--teal),var(--teal-deep))", opacity: v ? 1 : 0 }} />
              <div className="flex items-center justify-between">
                <span className="font-display text-[15px] font-bold">{u.symbol}</span>
                <span className={`text-[12px] tnum ${on ? "text-teal" : v > 0 ? "text-muted" : "text-subtle/60"}`}>{v}</span>
              </div>
              <div className="mt-0.5 truncate text-[11.5px] text-subtle">{u.name}</div>
            </button>
          );
        })}
      </div>

      {open && (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="go" onClick={submit} busy={busy} disabled={!dirty}>
            Submit {selected.size} pick{selected.size === 1 ? "" : "s"}
          </Button>
          {saved && !dirty && <span className="text-[13px] text-green">✓ saved</span>}
          {selected.size > 0 && (
            <span className="font-display text-[11.5px] text-subtle">{Array.from(selected).join(" · ")}</span>
          )}
        </div>
      )}
    </Card>
  );
}
