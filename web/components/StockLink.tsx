"use client";

// Any symbol on any page becomes a door into the stock detail panel: wrap it
// in <StockLink>. Default look = the existing Chip (teal outline pill); pass
// `unstyled` + your own classes to keep a row/table's typography and just make
// the symbol clickable. The panel itself is hosted once at the app level
// (StockPanelProvider) — this only calls openStock().

import { useStockPanel } from "@/components/StockPanel";

export function StockLink({
  symbol, children, className = "", unstyled = false,
}: {
  symbol: string;
  children?: React.ReactNode;
  className?: string;
  unstyled?: boolean;
}) {
  const { openStock } = useStockPanel();
  const base = unstyled
    ? "cursor-pointer"
    : "enter inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-teal/40 bg-teal/[0.1] px-2.5 py-1 font-display text-[12.5px] font-semibold text-ink transition hover:border-teal/70 hover:bg-teal/[0.16]";
  return (
    <button type="button" onClick={() => openStock(symbol)}
      aria-haspopup="dialog" aria-label={`View ${symbol} details`}
      className={`${base} ${className}`}>
      {children ?? symbol}
    </button>
  );
}
