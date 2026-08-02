"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Freshness } from "@/components/ui";
import { ClubPerformance } from "@/components/ClubPerformance";
import { PriceChart, type ChartLabelMode } from "@/components/PriceChart";
import { useStockPanel } from "@/components/StockPanel";
import { sectorOf, sectorColor } from "@/lib/sectors";
import { fmtDateTime } from "@/lib/format";
import type { EquityHistory, EquityPoint, EquityRangeKey } from "@/lib/types";

// ── the account line ────────────────────────────────────────────────────────
// Every point on it is an observation the club actually recorded (the 10-minute
// account refresher writes one each time it reads the balance). There is NO
// interpolation and no smoothing.
//
// It used to be both. The only account history that existed was one row a WEEK
// (voting_power_history.equity), and this component fanned ~6 of those weekly
// anchors out into ~105 evenly-spaced points by linear blending — so all but six
// of the values on a money chart were invented, which is exactly why it read as
// "straight segments between a handful of dots". Real curves need MORE REAL
// POINTS, not a prettier interpolation, so the fix is the recorded series and
// nothing else. A window with fewer than two recorded points draws no line at
// all and says so.
//
// (Backfill is impossible, not merely unimplemented: Robinhood exposes equity /
// option / index historicals but no account-value history, so the only way this
// gets denser is time passing.)
// 1D is deliberately absent: recording began 2026-08-01, so no full trading
// session exists yet and a 1D tab could only disappoint. The 10-minute
// recorder keeps accumulating precisely so it can return with real intraday
// shape.
const RANGES: EquityRangeKey[] = ["1W", "1M", "3M", "1Y", "ALL"];

type Cache = Partial<Record<EquityRangeKey, EquityPoint[]>>;

/** Defensive read of the equity payload: anything malformed or partially typed
 *  is dropped rather than plotted. Sorted, because the chart's x-axis is
 *  time-positioned and assumes monotonic timestamps.
 *
 *  $0.00 IS A REAL OBSERVATION and must survive. The backend only ever records
 *  a value it actually read — a failed read writes nothing, and never writes 0
 *  (services/equity_history.py). So a zero means the account really was empty.
 *  Filtering `v > 0` here silently deleted exactly the moment a member would
 *  most want to see: a liquidation recorded as [1000, 900, 0, 0, 0] drew only
 *  two vertices, the low label read $900, and the line simply stopped — a wiped
 *  account rendered as though nothing had happened. Only negatives (impossible)
 *  and non-finite values are rejected. */
function normalizeEquity(r: EquityHistory | null | undefined): EquityPoint[] {
  const raw: unknown = r?.points;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p: any) => ({ t: Number(p?.t), v: Number(p?.v) }))
    .filter((p) => Number.isFinite(p.t) && p.t > 0 && Number.isFinite(p.v) && p.v >= 0)
    .sort((a, b) => a.t - b.t);
}

/** Axis label format for a window. 1Y/ALL are adaptive because "ALL" is only as
 *  long as the club has been recording — MM/YYYY across a three-day record
 *  prints the same label at every tick. */
function labelModeFor(range: EquityRangeKey, series: EquityPoint[]): ChartLabelMode {
  if (range === "1W") return "weekday";
  if (range === "1M" || range === "3M") return "monthday";
  const span = series.length >= 2 ? series[series.length - 1].t - series[0].t : 0;
  if (span < 3 * 86400) return "time";
  if (span < 14 * 86400) return "weekday";
  // Past ~6 months MM/DD stops being unique across the window (two "08/01"
  // ticks a year apart), so the year has to be on the tick.
  if (span < 180 * 86400) return "monthday";
  return "monthyear";
}

const usd = (v: number) =>
  "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// The chart layer is inset from both ends of the stage so PriceChart's own
// labels land in clear air: the range tabs live at bottom: 92px (.perfx), and
// the floating nav pill covers the top ~65px — a peak drawn at the plot's top
// edge would otherwise disappear behind it.
const CHART_BOTTOM = 148;        // clearance for the axis + range controls
// The line lives in a BAND above the range controls, not the whole stage.
// The stage is viewport-height, so an uncapped layer made the chart ~900px
// tall on a desktop — a flat after-hours series rendered as a wall of teal
// fill with the line lost at the top (owner: "graph looks even worse").
// Robinhood's own account chart is a modest horizontal band under the hero
// number; 40vh capped at 380px reads the same at every window size.
const CHART_BAND = "clamp(260px, 52vh, 560px)";

// lighten/darken a hex by index so the stocks inside one sector stay visually
// related (same hue) but distinguishable.
function shade(hex: string, i: number, n: number): string {
  const f = n <= 1 ? 1 : 1.3 - (i / (n - 1)) * 0.68; // 1.30 (lighter) → 0.62 (darker)
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  const cl = (x: number) => Math.max(0, Math.min(255, Math.round(x * f)));
  const to = (x: number) => x.toString(16).padStart(2, "0");
  return `#${to(cl(r))}${to(cl(g))}${to(cl(b))}`;
}

export function CinematicOverview() {
  // Account + holdings come from the auth context's snapshot — fetched once at
  // sign-in and kept warm on a 5-minute poll. Navigating back here costs zero
  // broker reads (it used to cost ~4s of live Robinhood calls every visit).
  const {
    session, account: acc, holdings: hold, accountReady, accountError,
    accountAsOf, accountStale, accountRefreshing, refreshAccount,
  } = useAuth();
  const router = useRouter();
  const { openStock } = useStockPanel();  // stable callback from the app-level provider
  const root = useRef<HTMLDivElement>(null);
  const counted = useRef(false); // the hero count-up is a first-paint flourish, not a per-poll one
  const [lastPicks, setLastPicks] = useState<{ symbol: string; name?: string; weight?: number }[]>([]);
  const loaded = accountReady;              // the snapshot attempt has settled
  const failed = !acc && !!accountError;    // …and we still have no numbers at all

  useEffect(() => {
    // the most recent DECIDED basket — the donut's fallback when the account is
    // all cash AND the active ballot hasn't collected votes yet
    api.results().then((r) => setLastPicks(r.last_decided?.picks || [])).catch(() => {});
  }, []);

  const pnl = acc?.pnl;
  // The hero number = the member's REAL total account value (holdings + cash).
  const value = (acc?.allocation?.balance?.total_value ?? null)
    ?? (hold.length ? hold.reduce((a, h) => a + h.value, 0) : null)
    ?? (pnl && pnl.invested > 0 ? pnl.value : null)
    ?? acc?.allocation?.resolved_usd ?? 0;
  // donut + holdings reflect what you actually HOLD; fall back to today's forming
  // basket, then to the LAST DECIDED basket (early morning / post-liquidation the
  // active ballot is empty — never show a bare dash).
  const basket = ((session?.picks?.length ? session.picks : session?.tally) || []);
  const fallback = basket.length ? basket : lastPicks;
  const items = hold.length
    ? hold.map((h) => ({ sym: h.symbol, name: h.symbol, val: h.value, pnl: h.pnl }))
    : fallback.slice(0, 12).map((b) => ({ sym: b.symbol, name: b.name || b.symbol, val: b.weight || 1, pnl: null as number | null }));
  const itemsLabel = hold.length ? "holdings" : basket.length ? "names" : "last basket";
  const itemsKey = items.map((i) => `${i.sym}:${i.val}:${i.pnl ?? ""}`).join(",");
  const closesAt = session?.closes_at ?? 0;
  const open = session?.status === "open";
  const showStage = loaded && value > 0;

  // ── recorded equity history, one window at a time ─────────────────────────
  const [cache, setCache] = useState<Cache>({});
  const [range, setRange] = useState<EquityRangeKey>("1W");
  const [autoRange, setAutoRange] = useState(true);   // the thin-default → ALL fallback
  const [hover, setHover] = useState<number | null>(null);
  const [slide, setSlide] = useState(0);
  const [chartH, setChartH] = useState(0);
  const [countUp, setCountUp] = useState(0);          // 0→1 entrance multiplier

  // Fetch the selected window once and keep it. A failure caches an EMPTY
  // series on purpose: a 404 (older backend), a 500 and "you have no rows yet"
  // are the same thing from here — no history to draw — and the honest empty
  // state is better than a spinner that never resolves.
  useEffect(() => {
    if (!showStage || cache[range] !== undefined) return;
    let alive = true;
    api.myEquity(range)
      .then((r) => { if (alive) setCache((c) => ({ ...c, [range]: normalizeEquity(r) })); })
      .catch(() => { if (alive) setCache((c) => ({ ...c, [range]: [] })); });
    return () => { alive = false; };
  }, [showStage, range, cache]);

  // Recording started recently, so the default window can be legitimately
  // thin on a fresh account. Fall back ONCE to the widest window rather than
  // greeting a member who DOES have a record with an empty chart.
  useEffect(() => {
    if (!autoRange) return;
    const day = cache["1W"];
    if (day === undefined) return;
    if (day.length >= 2) { setAutoRange(false); return; }
    const all = cache["ALL"];
    if (all === undefined) { setRange("ALL"); return; }   // the fetch effect picks it up
    setAutoRange(false);
    if (all.length < 2) setRange("1W");                   // nothing anywhere: sit on the default
  }, [autoRange, cache]);

  // A window switch invalidates the hovered index.
  useEffect(() => { setHover(null); }, [range]);

  const series = useMemo(() => cache[range] ?? [], [cache, range]);
  const chartPoints = useMemo(() => series.map((p) => ({ t: p.t, c: p.v })), [series]);
  const anyHistory = RANGES.some((k) => (cache[k]?.length ?? 0) >= 2);
  // Don't judge a member historyless until the default window has answered AND
  // (if it was thin) ALL has too — else the club fallback flashes on load.
  const probeDone = cache["1W"] !== undefined && (anyHistory || cache["ALL"] !== undefined);
  const noHistory = probeDone && !anyHistory;
  const thisWindowEmpty = anyHistory && cache[range] !== undefined && series.length < 2;

  // Measure the chart layer: PriceChart draws in real pixels, so it needs a
  // height, and the stage is a viewport-sized hero that changes with the window.
  const chartBox = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = chartBox.current;
    if (!el) return;
    const m = () => setChartH(Math.round(el.getBoundingClientRect().height));
    m();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(m) : null;
    ro?.observe(el);
    window.addEventListener("resize", m);
    return () => { ro?.disconnect(); window.removeEventListener("resize", m); };
  }, [showStage, anyHistory]);

  // Count the hero up ONCE per visit. The snapshot refreshes on a 5-minute
  // poll, and replaying a 0→$23,567 count-up every five minutes reads as a glitch.
  useEffect(() => {
    if (!showStage) return;
    const RM = typeof window !== "undefined" && window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (RM || counted.current) { setCountUp(1); return; }
    counted.current = true;
    let raf = 0, t0 = 0;
    const step = (ts: number) => {
      if (!t0) t0 = ts;
      const p = Math.min((ts - t0) / 950, 1);
      setCountUp(1 - Math.pow(1 - p, 3));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [showStage]);

  // ── the readout above the line ────────────────────────────────────────────
  const hoverPt = hover != null && hover >= 0 && hover < series.length ? series[hover] : null;
  const shownV = hoverPt ? hoverPt.v : value;
  const baseV = series.length >= 2 ? series[0].v : null;
  const dV = baseV != null ? shownV - baseV : null;
  const pV = baseV ? (shownV / baseV - 1) * 100 : null;
  const upRange = (dV ?? 0) >= 0;
  const heroV = hoverPt ? hoverPt.v : value * countUp;

  useEffect(() => {
    const r = root.current;
    if (!r) return;
    const $ = (sel: string) => r.querySelector(sel) as any;

    // ── donut: holdings grouped by SECTOR; click a sector to morph into the
    //    individual stocks within it, "‹ All sectors" to morph back ──
    type Slc = { key: string; label: string; sub: string; pc: number; c: string; drill: boolean };
    const bySector: Record<string, { sector: string; val: number; items: typeof items }> = {};
    for (const it of items) {
      const sec = sectorOf(it.sym);
      (bySector[sec] ||= { sector: sec, val: 0, items: [] });
      bySector[sec].val += (it.val || 0);
      bySector[sec].items.push(it);
    }
    const sectorGroups = Object.values(bySector).sort((a, b) => b.val - a.val);
    const GTOT = sectorGroups.reduce((a, g) => a + (g.val || 0), 0) || 1;
    const pctOf = (v: number, tot: number) => Math.round((v / (tot || 1)) * 1000) / 10;

    const CC = 2 * Math.PI * 80, GAP = 2.2, arcsEl = $("#arcs"), legEl = $("#leg"), ringEl = $("#donutring");
    function paintArcs(slices: Slc[]) {
      while (arcsEl.children.length < slices.length)
        arcsEl.insertAdjacentHTML("beforeend", `<circle class="arc" cx="100" cy="100" r="80" stroke="#2DD4BF" stroke-dasharray="0 ${CC.toFixed(2)}"/>`);
      let off = 0;
      for (let i = 0; i < arcsEl.children.length; i++) {
        const c = arcsEl.children[i] as any;
        if (i < slices.length && slices[i].pc > 0) {
          const len = slices[i].pc / 100 * CC;
          // draw slightly short of the full segment so neighbours don't share an
          // anti-aliased seam (the high-contrast edges were reading as a notch);
          // a clean butt cap + tiny gap = crisp straight radial edges.
          const draw = Math.max(1, len - GAP);
          c.setAttribute("stroke", slices[i].c);
          c.setAttribute("stroke-dasharray", draw.toFixed(2) + " " + (CC - draw).toFixed(2));
          c.setAttribute("stroke-dashoffset", (-off).toFixed(2));
          c.style.cursor = slices[i].drill ? "pointer" : "default";
          c.onclick = slices[i].drill ? (e: any) => { e.stopPropagation(); drillInto(slices[i].key); } : null;
          off += len;
        } else { c.setAttribute("stroke-dasharray", "0 " + CC.toFixed(2)); c.onclick = null; }
      }
    }
    function paintLegend(slices: Slc[], sector: string | null) {
      legEl.style.opacity = "0";
      setTimeout(() => {
        const back = sector ? `<button class="legback" data-back="1"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>All sectors</button>` : "";
        legEl.innerHTML = back + slices.map((x) =>
          `<div class="row" ${x.drill ? `data-sec="${x.key}"` : ""}><span class="sw" style="background:${x.c};color:${x.c}"></span><span class="nm">${x.label}${x.sub ? `<s>${x.sub}</s>` : ""}</span><span class="pc tnum">${x.pc}%</span></div>`).join("");
        legEl.style.opacity = "1";
        (legEl.querySelector("[data-back]") as HTMLElement | null)?.addEventListener("click", (e) => { e.stopPropagation(); renderSectors(); });
        legEl.querySelectorAll("[data-sec]").forEach((el: any) => el.addEventListener("click", (e: any) => { e.stopPropagation(); drillInto(el.getAttribute("data-sec")); }));
      }, 150);
    }
    function pop() { ringEl.classList.remove("pop"); void (ringEl as HTMLElement).offsetWidth; ringEl.classList.add("pop"); }
    function renderSectors() {
      if (!items.length) {
        // nothing held, no basket anywhere yet — the account is 100% settled cash
        const cash: Slc = { key: "cash", label: "Cash", sub: "the next buy cycle deploys it",
                            pc: 100, c: "#3d4a59", drill: false };
        $("#dctr").textContent = "100%";
        $("#dctrs").textContent = "cash";
        paintArcs([cash]); paintLegend([cash], null); pop();
        return;
      }
      const slices: Slc[] = sectorGroups.map((g) => ({
        key: g.sector, label: g.sector, sub: `${g.items.length} name${g.items.length === 1 ? "" : "s"}`,
        pc: pctOf(g.val, GTOT), c: sectorColor(g.sector), drill: g.items.length > 0,
      }));
      $("#dctr").textContent = String(items.length);
      $("#dctrs").textContent = itemsLabel;
      paintArcs(slices); paintLegend(slices, null); pop();
    }
    function drillInto(sector: string) {
      const g = bySector[sector]; if (!g) return;
      const sorted = [...g.items].sort((a, b) => (b.val || 0) - (a.val || 0));
      const tot = g.val || sorted.reduce((a, x) => a + (x.val || 0), 0) || 1;
      const base = sectorColor(sector);
      const slices: Slc[] = sorted.map((it, i) => ({
        key: it.sym, label: it.sym, sub: (it.name && it.name !== it.sym) ? it.name : "",
        pc: pctOf(it.val || 0, tot), c: shade(base, i, sorted.length), drill: false,
      }));
      $("#dctr").textContent = String(g.items.length);
      $("#dctrs").textContent = sector;
      paintArcs(slices); paintLegend(slices, sector); pop();
    }

    // ── holdings ──
    // Real per-position P&L (from /api/me/holdings) fills the middle of the row;
    // basket-fallback rows (not positions, no P&L) get a plain spacer instead.
    const pnlCell = (p: number | null) => p == null
      ? `<span class="tnum" style="flex:1;text-align:right;font:600 14px/1 var(--font-outfit);color:var(--muted)">—</span>`
      : `<span class="tnum" style="flex:1;text-align:right;font:600 14px/1 var(--font-outfit);color:${p >= 0 ? "var(--green)" : "var(--red)"}">${p >= 0 ? "+" : "-"}$${Math.abs(p).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>`;
    $("#holdings").innerHTML = items.length
      ? items.map((h) => `<div class="holdrow" data-stock="${h.sym}"><span class="htk">${h.sym}</span><div class="hn"><b>${h.name || h.sym}</b><s>${h.sym}</s></div>${hold.length ? pnlCell(h.pnl) : `<span style="flex:1"></span>`}${hold.length ? `<span class="hwt tnum">$${Math.round(h.val || 0).toLocaleString()}</span>` : ""}</div>`).join("")
      : `<div style="color:var(--subtle);font:500 13px/1.6 var(--font-inter);padding:8px 2px">No holdings yet. <a href="/vote" style="color:var(--teal)">Cast your vote →</a></div>`;

    renderSectors();

    // ── carousel ──
    const stage = $("#stage");
    const dots = [...r.querySelectorAll(".dots button")] as any[];
    // Read the slide back off the DOM: this effect re-runs when holdings change,
    // and restarting at 0 would desync `cur` (and now React's `slide`) from the
    // slide the member is actually looking at.
    let cur = Number(stage.dataset.s) || 0;
    const go = (i: number) => {
      cur = (i + 3) % 3;
      stage.dataset.s = String(cur);
      setSlide(cur);
      dots.forEach((d, kk) => d.classList.toggle("on", kk === cur));
    };
    const next = () => go(cur + 1), prev = () => go(cur - 1);
    $("#next").addEventListener("click", (e: any) => { e.stopPropagation(); next(); });
    $("#prev").addEventListener("click", (e: any) => { e.stopPropagation(); prev(); });
    dots.forEach((d, kk) => d.addEventListener("click", (e: any) => { e.stopPropagation(); go(kk); }));
    const onKey = (e: KeyboardEvent) => { if (e.key === "ArrowRight") next(); else if (e.key === "ArrowLeft") prev(); };
    document.addEventListener("keydown", onKey);
    $("#donutring").addEventListener("click", (e: any) => e.stopPropagation());
    const onNav = (e: any) => {
      // a holdings/consensus row opens the stock detail panel…
      const st = e.target.closest("[data-stock]");
      if (st) { openStock(st.getAttribute("data-stock") || ""); return; }
      // …everything else tagged for voting still goes to the ballot
      if (e.target.closest("[data-vote],.strip a")) router.push("/vote");
    };
    r.addEventListener("click", onNav);

    // drag / wheel / touch
    let down = false, x0 = 0, moved = 0;
    const pd = (e: PointerEvent) => { if ((e.target as HTMLElement).closest(".arw,.dots,.strip a,.tf,.donutring,.leg,.scrolldn,a,button")) return; down = true; x0 = e.clientX; moved = 0; stage.classList.add("grab"); };
    const pm = (e: PointerEvent) => { if (down) moved = e.clientX - x0; };
    const pu = () => { if (!down) return; down = false; stage.classList.remove("grab"); if (Math.abs(moved) > 45) go(cur + (moved < 0 ? 1 : -1)); };
    stage.addEventListener("pointerdown", pd); window.addEventListener("pointermove", pm); window.addEventListener("pointerup", pu);
    const wlock = { v: false };
    const onWheel = (e: WheelEvent) => { if (Math.abs(e.deltaX) > Math.abs(e.deltaY) * 1.6 && Math.abs(e.deltaX) > 22) { e.preventDefault(); if (!wlock.v) { wlock.v = true; go(cur + (e.deltaX > 0 ? 1 : -1)); setTimeout(() => (wlock.v = false), 480); } } };
    stage.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      document.removeEventListener("keydown", onKey);
      r.removeEventListener("click", onNav);
      window.removeEventListener("pointermove", pm); window.removeEventListener("pointerup", pu);
      stage.removeEventListener("pointerdown", pd);
      stage.removeEventListener("wheel", onWheel as any);
    };
    // `showStage` matters: the stage only mounts after it flips, and that render
    // can have identical item deps — without it the effect would never
    // initialize the freshly-mounted stage.
  }, [showStage, itemsKey, itemsLabel, router, openStock]); // eslint-disable-line react-hooks/exhaustive-deps

  // Wait for the account + holdings fetches before choosing what to show — a
  // funded member should never flash the zero-state (or a $0 hero) while loading.
  if (!loaded) return null;

  if (value <= 0) {
    if (failed) {
      return (
        <div className="glass shine enter mt-4 p-6 sm:p-7">
          <div className="text-[13px] text-red">⚠ Couldn&apos;t load your account right now.</div>
        </div>
      );
    }
    // Connected but nothing invested yet: a quiet zero-state instead of a dead
    // $0 cinematic stage.
    return (
      <div className="glass shine enter mt-4 p-6 sm:p-7">
        <div className="flex items-center gap-2.5">
          <span className="dot bg-teal pulse" />
          <h2 className="font-display text-[16px] font-bold">You&apos;re connected</h2>
        </div>
        <p className="mt-2 max-w-[58ch] text-[13px] leading-relaxed text-muted">
          Commit an amount in{" "}
          <Link href="/account" className="text-teal">Account</Link> and flip Participate on —
          your first basket buys on the next buy day.
        </p>
        {/* "nothing invested" is itself a money claim — date it. */}
        <Freshness asOf={accountAsOf} stale={accountStale} busy={accountRefreshing}
          onRefresh={refreshAccount} className="mt-3" />
        {/* Nothing of yours has traded yet, so the club's real record is what
            there is to show. */}
        <div className="mt-5 border-t border-white/10 pt-5">
          <ClubPerformance />
        </div>
      </div>
    );
  }

  const labelMode = labelModeFor(range, series);
  const layer = (extra: React.CSSProperties = {}): React.CSSProperties => ({
    position: "absolute", left: 0, right: 0, bottom: CHART_BOTTOM,
    height: CHART_BAND, top: "auto",
    transition: "opacity .34s var(--ease)", ...extra,
  });

  return (
    <div className="cine" ref={root}>
      <section id="stage" data-s="0">
        {/* The account line. Deliberately NOT inside the old `.chartfield`
            wrapper: that CSS force-stretches any descendant <svg> to 100%×100%,
            which fights PriceChart's measured pixel viewBox. */}
        <div ref={chartBox} style={layer({
          zIndex: 0,
          opacity: slide === 1 ? 1 : slide === 0 ? 0.2 : 0,
          pointerEvents: slide === 1 ? "auto" : "none",
        })}>
          {chartH > 80 && series.length >= 2 && (
            <PriceChart
              key={range}
              points={chartPoints}
              height={chartH}
              interactive={slide === 1}
              labelMode={labelMode}
              onHover={setHover}
              ariaLabel={`account value, ${fmtDateTime(series[0].t)} to ${fmtDateTime(series[series.length - 1].t)}`}
            />
          )}
        </div>

        {/* You have a record, just not inside THIS window. */}
        {thisWindowEmpty && (
          <div style={layer({
            zIndex: 2, display: "grid", placeItems: "center", padding: "0 24px",
            textAlign: "center", pointerEvents: "none", opacity: slide === 1 ? 1 : 0,
          })}>
            <div style={{ maxWidth: "44ch", font: "500 13px/1.6 var(--font-inter)", color: "var(--subtle)" }}>
              Not enough history yet in this window — this fills in as the club records your balance.
            </div>
          </div>
        )}

        {/* No record anywhere yet. Not an error: the club can only record your
            balance going forward, so this is simply what a young account looks
            like. The club's own real chart stands in the empty space. */}
        {noHistory && (
          <div className="clubfill">
            <div className="mb-3 text-[12.5px] leading-relaxed text-subtle">
              Not enough history yet — this fills in as the club records your balance.
            </div>
            <ClubPerformance />
          </div>
        )}

        {/* The line now runs BEHIND this block at full opacity, and a red
            change on a red area fill is close to unreadable — hence a scrim
            behind the text. drop-shadow, NOT text-shadow: the hero digits are
            background-clip:text with color:transparent, and a text-shadow
            paints THROUGH transparent glyphs — it sat on top of the white→teal
            gradient and turned the number smoky grey (the owner read it as a
            font/color change). drop-shadow works on the rendered pixels, so
            the gradient stays exactly as designed. */}
        <div className="value" style={{ filter: "drop-shadow(0 2px 14px rgba(4,7,11,0.9))" }}>
          <div className="num tnum" id="val">{usd(heroV)}</div>
          <div className={`chg tnum${upRange ? "" : " dn"}`}>
            {dV != null && pV != null ? (
              <>
                {upRange ? "▲ " : "▼ "}
                {dV >= 0 ? "" : "-"}${Math.abs(dV).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                {" · "}{pV >= 0 ? "+" : ""}{pV.toFixed(2)}%
              </>
            ) : " "}
          </div>
          {hoverPt && (
            // 20px, not 13: `.value` is scale(.5) on the chart slide, so this
            // is the size the scrub date actually renders at next to a 22px
            // change line.
            <div className="tnum" style={{ marginTop: 10, font: "500 20px/1 var(--font-inter)", color: "var(--subtle)" }}>
              {fmtDateTime(hoverPt.t)}
            </div>
          )}
        </div>

        <div className="strip">
          <a data-vote="1"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="3" /><path d="m8 12 3 3 5-6" /></svg>Vote</a>
          <a data-vote="1"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" /><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" /><path d="M4 22h16" /><path d="M18 2H6v7a6 6 0 0 0 12 0Z" /></svg>Basket</a>
        </div>

        <div className="cycle">
          <span className="dot bg-teal pulse" />
          {open ? <>Cycle closes in <b><CountdownText to={closesAt} /></b></> : <>voting {session?.status || "closed"}</>}
        </div>

        <div className="perfx">
          {/* Windows only mean something over a line of your own. */}
          {anyHistory && (
            <div className="tfs" id="tfs" style={{ flexWrap: "wrap" }}>
              {RANGES.map((t) => (
                <button key={t} type="button" className={`tf ${t === range ? "on" : ""}`}
                  onClick={(e) => { e.stopPropagation(); setAutoRange(false); setRange(t); }}>
                  {t}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="alloc">
          <div className="donutring" id="donutring"><svg viewBox="0 0 200 200"><g id="arcs" /></svg><div className="ctr"><b id="dctr">0</b><span id="dctrs">names</span></div></div>
          <div className="leg" id="leg" />
        </div>

        <button className="arw l" id="prev"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></svg></button>
        <button className="arw r" id="next"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg></button>
        <div className="dots" id="dots"><button className="on" /><button /><button /></div>
        <button className="scrolldn" id="scrolldn" aria-label="holdings" onClick={() => root.current?.querySelector("#hold")?.scrollIntoView({ behavior: "smooth" })}>
          <svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg>
        </button>
      </section>

      <section id="hold">
        {/* The dollars above and below are a snapshot, not a live tape — say when
            it was taken. Footnote-quiet, and only turns amber when it's old. */}
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div className="label">Consensus basket</div>
          <Freshness asOf={accountAsOf} stale={accountStale} busy={accountRefreshing} onRefresh={refreshAccount} />
        </div>
        <div id="holdings" />
      </section>
    </div>
  );
}

function CountdownText({ to }: { to: number }) {
  const [, force] = useState(0);
  useEffect(() => { const id = setInterval(() => force((x) => x + 1), 1000); return () => clearInterval(id); }, []);
  const s = Math.max(0, to - Math.floor(Date.now() / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return <>{h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`}</>;
}
