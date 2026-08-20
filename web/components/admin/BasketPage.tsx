"use client";

// /basket — what the club actually holds by decision, and what the open ballot
// is shaping up to replace it with. Two columns, same row grammar: rank ·
// symbol · name · weight bar. The standing basket comes from the last CLOSED
// session's picks_json (panel.basket); the forming one is the open session's
// live tally (overview.session.tally). Either side degrades to a quiet note.

import type { AdminOverview } from "@/lib/api";
import type { AdminPanel } from "@/lib/types";
import { Card } from "@/components/ui";
import { SectionLabel } from "./util";

type Pick = { symbol: string; name?: string | null; votes?: number | null; weight?: number | null };

function Rows({ picks }: { picks: Pick[] }) {
  const maxW = Math.max(...picks.map((p) => p.weight || 0), 1);
  return (
    <div className="mt-1">
      {picks.map((p, i) => (
        <div key={p.symbol}
          className="flex items-center gap-3 border-t border-hair py-2 first:border-t-0">
          <span className="w-5 flex-none text-right text-[11px] tnum text-subtle">{i + 1}</span>
          <span className="w-14 flex-none font-mono text-[13px] font-semibold text-ink">{p.symbol}</span>
          <span className="hidden min-w-0 flex-none basis-40 truncate text-[12px] text-muted sm:block">
            {p.name && p.name !== p.symbol ? p.name : ""}
          </span>
          <div className="h-[5px] min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
            <div className="h-full rounded-full bg-teal/60"
              style={{ width: `${((p.weight || 0) / maxW) * 100}%` }} />
          </div>
          <span className="w-14 flex-none text-right text-[11.5px] tnum text-muted">
            {p.weight != null ? `$${Math.round(p.weight)}` : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function BasketPage({ panel, overview, overviewErr }: {
  panel: AdminPanel;
  overview: AdminOverview | null;
  overviewErr: string | null;
}) {
  const standing = (panel as AdminPanel & { basket?: { trade_date: string; picks: Pick[] } | null }).basket;
  const tally = (overview?.session?.tally ?? []) as Pick[];
  const open = overview?.session?.status === "open";

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <Card>
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel>Standing basket</SectionLabel>
          {standing && <span className="text-[11.5px] tnum text-subtle">decided {standing.trade_date}</span>}
        </div>
        {standing?.picks?.length ? (
          <Rows picks={standing.picks} />
        ) : (
          <p className="text-[12.5px] text-subtle">no basket decided yet</p>
        )}
      </Card>

      <Card>
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel>{open ? "Forming — open ballot" : "Last ballot tally"}</SectionLabel>
          {overview?.session && (
            <span className="text-[11.5px] tnum text-subtle">
              {overview.session.voters ?? 0} voter{(overview.session.voters ?? 0) === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {tally.length ? (
          <Rows picks={tally} />
        ) : (
          <p className="text-[12.5px] text-subtle">
            {overviewErr ? "unavailable — overview failed" : "no votes yet"}
          </p>
        )}
      </Card>
    </div>
  );
}
