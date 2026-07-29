"use client";

// The one stock-detail surface for the whole app: a slide-over hosted ONCE at
// the app level (StockPanelProvider in layout.tsx). Pages never mount their own
// panel — they call useStockPanel().openStock("NVDA") (usually via <StockLink>)
// and this provider renders the single instance.
//
// The overlay is createPortal'd to document.body ON PURPOSE: in-page fixed
// overlays break under ancestor transforms (the app's animations create
// containing blocks), so position:fixed only behaves from the body.

import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "@/lib/api";
import type { StockDetail, StockPricePoint, StockRangeKey } from "@/lib/types";
import { Freshness, Pill } from "@/components/ui";

// ── context ────────────────────────────────────────────────────────────────

type PanelCtx = { openStock: (symbol: string) => void };
const Ctx = createContext<PanelCtx | null>(null);

export function useStockPanel(): PanelCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useStockPanel must be used within StockPanelProvider");
  return c;
}

export function StockPanelProvider({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const openStock = useCallback((s: string) => {
    const sym = (s || "").trim().toUpperCase();
    if (sym) setSymbol(sym);
  }, []);
  const close = useCallback(() => setSymbol(null), []);
  return (
    <Ctx.Provider value={{ openStock }}>
      {children}
      {symbol && <StockPanel symbol={symbol} onClose={close} />}
    </Ctx.Provider>
  );
}

// ── formatting ─────────────────────────────────────────────────────────────

/** 2641000000000 → "2.64T", 45_100_000_000 → "45.1B", 1_200_000 → "1.2M". */
function fmtBig(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const f = (v: number) => {
    const a = Math.abs(v);
    return String(parseFloat(a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2)));
  };
  if (abs >= 1e12) return `${f(n / 1e12)}T`;
  if (abs >= 1e9) return `${f(n / 1e9)}B`;
  if (abs >= 1e6) return `${f(n / 1e6)}M`;
  if (abs >= 1e3) return `${f(n / 1e3)}K`;
  return String(parseFloat(n.toFixed(2)));
}

function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(iso: string, withYear = false): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}),
  });
}

// ── hand-rolled line chart (no chart library in this app) ──────────────────

/** Catmull-Rom → cubic beziers: a smooth line through every close. */
function smoothPath(pts: [number, number][]): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1],
      p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}

function RangeChart({ points }: { points: StockPricePoint[] }) {
  if (points.length < 2) {
    return (
      <div className="grid h-[168px] place-items-center rounded-[12px] border border-hair bg-white/[0.02] text-[12px] text-subtle">
        no price history for this range
      </div>
    );
  }
  const W = 400, H = 160, PAD = 10;
  const cs = points.map((p) => p.c);
  const mn = Math.min(...cs), mx = Math.max(...cs), span = mx - mn || 1;
  const X = (i: number) => (i / (points.length - 1)) * W;
  const Y = (c: number) => PAD + (1 - (c - mn) / span) * (H - 2 * PAD);
  const pts: [number, number][] = cs.map((c, i) => [X(i), Y(c)]);
  const up = cs[cs.length - 1] >= cs[0];
  const color = up ? "#2dd4bf" : "#ff6b6b";
  const line = smoothPath(pts);
  const area = `${line} L${W},${H} L0,${H} Z`;
  return (
    <div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
          className="block h-[160px] w-full" aria-hidden>
          <defs>
            <linearGradient id="spanel-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity="0.22" />
              <stop offset="1" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          {/* hairline grid */}
          {[0.25, 0.5, 0.75].map((t) => (
            <line key={t} x1="0" x2={W} y1={t * H} y2={t * H}
              stroke="rgba(255,255,255,0.07)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          <path d={area} fill="url(#spanel-fill)" />
          <path d={line} fill="none" stroke={color} strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
        {/* min/max of the visible range */}
        <span className="absolute right-1 top-0 text-[10.5px] text-subtle tnum">{fmtPrice(mx)}</span>
        <span className="absolute bottom-0 right-1 text-[10.5px] text-subtle tnum">{fmtPrice(mn)}</span>
      </div>
      {/* endpoints spanning a year boundary carry the year — "Jul 28" alone
          would read as last month on the 1Y tab */}
      {(() => {
        const wy = points[0].d.slice(0, 4) !== points[points.length - 1].d.slice(0, 4);
        return (
          <div className="mt-1.5 flex justify-between text-[10.5px] text-subtle tnum">
            <span>{fmtDate(points[0].d, wy)}</span>
            <span>{fmtDate(points[points.length - 1].d, wy)}</span>
          </div>
        );
      })()}
    </div>
  );
}

// ── the panel ──────────────────────────────────────────────────────────────

const RANGES: StockRangeKey[] = ["1M", "3M", "1Y"];

function Skeleton() {
  return (
    <div className="space-y-5" aria-hidden>
      <div className="pulse h-8 w-40 rounded-lg bg-white/[0.07]" />
      <div className="pulse h-10 w-52 rounded-lg bg-white/[0.07]" />
      <div className="pulse h-[168px] rounded-[12px] bg-white/[0.05]" />
      <div className="grid grid-cols-2 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="pulse h-12 rounded-[11px] bg-white/[0.05]" />
        ))}
      </div>
    </div>
  );
}

function StockPanel({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [data, setData] = useState<StockDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const [range, setRange] = useState<StockRangeKey>("1M");
  const [expanded, setExpanded] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // fetch per symbol; reset the view state on every new symbol
  useEffect(() => {
    let alive = true;
    setData(null); setState("loading"); setErr(null); setRange("1M"); setExpanded(false);
    api.stockDetail(symbol)
      .then((d) => { if (alive) { setData(d); setState("ready"); } })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) setState("missing");
        else { setState("error"); setErr(e instanceof Error ? e.message : "couldn't load this stock"); }
      });
    return () => { alive = false; };
  }, [symbol]);

  // Esc closes; Tab is trapped inside the dialog — without the trap, tabbing
  // walks focus into the page behind the backdrop while the panel is modal.
  // (Focus-return on close lives in the mount effect below, untouched.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
        'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (focusables.length === 0) { e.preventDefault(); panel.focus(); return; }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // Wrap at either edge; also pull focus back in if it's outside the panel
      // (or on the panel itself, whose natural shift-tab would walk out).
      if (e.shiftKey) {
        if (!active || !panel.contains(active) || active === first || active === panel) {
          e.preventDefault(); last.focus();
        }
      } else if (!active || !panel.contains(active) || active === last) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // take focus on open, hand it back on close; lock the page scroll behind us
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, []);

  if (typeof document === "undefined") return null;

  const q = data?.quote;
  const up = (q?.change_pct ?? 0) >= 0;
  const f = data?.fundamentals;
  const desc = f?.description || "";
  const longDesc = desc.length > 220;
  const facts: [string, string][] = f ? [
    ["Mkt cap", fmtBig(f.market_cap)],
    ["P/E", f.pe_ratio != null ? String(parseFloat(f.pe_ratio.toFixed(1))) : "—"],
    ["Div yield", f.dividend_yield != null ? `${f.dividend_yield.toFixed(2)}%` : "—"],
    ["Sector", f.sector || "—"],
    ["52w high", fmtPrice(f.high_52w)],
    ["52w low", fmtPrice(f.low_52w)],
    ["Volume", fmtBig(f.volume)],
    ["Avg vol", fmtBig(f.avg_volume)],
  ] : [];

  return createPortal(
    <>
      <div className="sbackdrop" onClick={onClose} aria-hidden />
      <div ref={panelRef} className="spanel" role="dialog" aria-modal="true"
        aria-label={`${symbol} details`} tabIndex={-1}>
        <div className="flex min-h-full flex-col p-5 sm:p-6">
          {/* header row: symbol + close, always present */}
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2.5">
                <h2 className="font-display text-[24px] font-bold leading-none tracking-tight">{symbol}</h2>
                {data?.in_universe && <Pill tone="teal">votable</Pill>}
              </div>
              {data && <div className="mt-1.5 truncate text-[13px] text-muted">{data.name}</div>}
            </div>
            <button onClick={onClose} aria-label="Close"
              className="grid h-8 w-8 flex-none place-items-center rounded-full border border-hair text-muted transition hover:border-white/25 hover:text-ink">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>

          <div className="mt-5 flex-1">
            {state === "loading" && <Skeleton />}

            {state === "missing" && (
              <div className="rounded-[14px] border border-hair bg-white/[0.03] p-5 text-center">
                <div className="font-display text-[15px] font-bold">No such symbol</div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-subtle">
                  “{symbol}” isn&apos;t in the club universe and the broker doesn&apos;t price it.
                </p>
              </div>
            )}

            {state === "error" && (
              <div className="rounded-[14px] border border-red/30 bg-red/[0.07] p-4 text-[13px] text-red">
                ⚠ Couldn&apos;t load {symbol} — {err}
              </div>
            )}

            {state === "ready" && data && (
              <div className="space-y-6">
                {/* price + day change */}
                <div>
                  <div className="hero-num sm tnum">{fmtPrice(q?.price)}</div>
                  {q?.change_pct != null && (
                    <div className={`mt-1.5 text-[13.5px] font-semibold tnum ${up ? "text-teal" : "text-red"}`}>
                      {up ? "▲" : "▼"} {up ? "+" : ""}{q.change_pct.toFixed(2)}% today
                      {q.prev_close != null && (
                        <span className="ml-2 font-medium text-subtle">prev close {fmtPrice(q.prev_close)}</span>
                      )}
                    </div>
                  )}
                </div>

                {/* range tabs + chart */}
                <div>
                  <div className="mb-2 flex gap-1.5">
                    {RANGES.map((r) => (
                      <button key={r} className={`tf ${r === range ? "on" : ""}`} onClick={() => setRange(r)}>
                        {r}
                      </button>
                    ))}
                  </div>
                  <RangeChart points={data.range?.[range] ?? []} />
                </div>

                {/* fundamentals */}
                <div>
                  <div className="label mb-2.5">Fundamentals</div>
                  <div className="grid grid-cols-2 gap-2.5">
                    {facts.map(([k, v]) => (
                      <div key={k} className="rounded-[11px] border border-hair bg-white/[0.03] px-3 py-2.5">
                        <div className="text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{k}</div>
                        <div className="mt-1 truncate font-display text-[14px] font-semibold tnum" title={v}>{v}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* description, clamped with a "more" toggle when long */}
                {desc && (
                  <div>
                    <div className="label mb-2">About</div>
                    <p className="text-[12.5px] leading-relaxed text-muted"
                      style={!expanded && longDesc ? {
                        display: "-webkit-box", WebkitLineClamp: 4,
                        WebkitBoxOrient: "vertical", overflow: "hidden",
                      } : undefined}>
                      {desc}
                    </p>
                    {longDesc && (
                      <button onClick={() => setExpanded((v) => !v)}
                        className="mt-1.5 text-[12px] font-semibold text-teal hover:underline">
                        {expanded ? "less" : "more"}
                      </button>
                    )}
                  </div>
                )}

                <Freshness asOf={data.as_of ?? null} />
              </div>
            )}
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
