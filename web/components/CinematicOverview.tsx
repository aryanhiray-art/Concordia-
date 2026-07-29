"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Freshness } from "@/components/ui";
import { sectorOf, sectorColor } from "@/lib/sectors";

// A representative market shape; we compress its daily returns to the member's
// real club P&L and scale it to end at their real club value.
const SPY = [680.73,678.87,671.40,676.47,680.59,684.83,687.96,690.38,690.31,687.85,687.01,681.92,683.17,687.72,691.81,689.58,689.51,694.07,695.16,693.77,690.36,692.24,691.66,677.58,685.40,688.98,689.23,692.73,695.49,695.42,694.04,691.97,695.41,689.53,686.19,677.62,690.62,693.95,692.12,691.96,681.27,681.75,682.85,686.29,684.48,689.43,682.39,687.35,693.15,689.30,685.99,686.38,680.33,685.13,681.31,672.38,678.27,677.18,676.33,666.06,662.29,669.03,670.79,661.43,659.80,648.57,655.38,653.18,656.82,645.09,650.34,655.24,655.83,658.93,659.22,676.01,679.91,679.46,686.10,694.46,699.94,701.66,710.14,708.72,711.21,713.94,715.17,718.66,720.65,723.77,733.83,737.62,739.30,742.31,748.17,738.65,741.25,745.64,750.59,754.60,756.48,759.57,757.09,737.55,741.75];

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
  const pctTarget = (pnl && pnl.invested > 0 && pnl.profit_pct != null) ? pnl.profit_pct : 0;
  // Real weekly account-value history (voting_power_history.equity) → the line
  // plots ACTUAL values, not a canned market shape, once there are ≥2 points.
  const equityHist = (acc?.power?.history || [])
    .map((h) => h.equity).filter((e): e is number => e != null && e > 0);
  const histKey = equityHist.join(",");
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

  useEffect(() => {
    const r = root.current;
    if (!r || value <= 0) return;
    const RM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const $ = (sel: string) => r.querySelector(sel) as any;
    const W = 1000, H = 600;

    // ── build the value series ──
    const N = SPY.length;
    let FUSD: number[];
    const anchors = equityHist.length ? [...equityHist, value] : [];
    if (anchors.length >= 2) {
      // REAL: interpolate the member's ACTUAL weekly account values to N smooth
      // points, ending exactly at the live current value.
      FUSD = [];
      const segs = anchors.length - 1;
      for (let i = 0; i < N; i++) {
        const t = (i / (N - 1)) * segs;
        const lo = Math.min(segs - 1, Math.floor(t)), f = t - lo;
        FUSD.push(anchors[lo] * (1 - f) + anchors[lo + 1] * f);
      }
    } else {
      // fallback (no history yet): a representative market shape scaled to the real
      // current value + return, so at least the endpoints are exact.
      const spyTot = SPY[N - 1] / SPY[0] - 1;
      const k = spyTot !== 0 ? (pctTarget / 100) / spyTot : 0;
      const fund = [1];
      for (let i = 1; i < N; i++) fund.push(fund[i - 1] * (1 + (SPY[i] / SPY[i - 1] - 1) * (k || 0.18)));
      const fsc = value / fund[N - 1];
      FUSD = fund.map((v) => v * fsc);
    }
    const DATES: string[] = [];
    const d0 = new Date(2026, 2, 1);
    for (let j = 0; j < N; j++) DATES.push(new Date(d0.getTime() + j * 1.4 * 864e5).toLocaleDateString("en-US", { month: "short", day: "numeric" }));
    const TF: Record<string, number> = { "1D": 6, "1W": 9, "1M": 22, "3M": 64, "1Y": N, ALL: N };
    const TFAX: Record<string, string[]> = { "1D": ["10AM","12PM","2PM","4PM"], "1W": ["Mon","Wed","Fri"], "1M": ["Wk1","Wk2","Wk3","Wk4"], "3M": ["3mo","2mo","now"], "1Y": ["Q1","Q2","Q3","now"], ALL: ["start","now"] };
    let fundPts: number[][] = [], usd: number[] = [], sliceStart = 0;
    const fmt = (v: number) => "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const setColor = (up: boolean) => { const c = up ? "#2DD4BF" : "#8A94A6"; $("#fundpath")?.setAttribute("stroke", c); $("#fc0")?.setAttribute("stop-color", c); $("#fc1")?.setAttribute("stop-color", c); };
    function readout(i: number) {
      const v = usd[i], base = usd[0], dv = v - base, pct = (v / base - 1) * 100, up = v >= base;
      $("#val").textContent = fmt(v);
      const c = $("#chg");
      c.innerHTML = (up ? "▲ " : "▼ ") + (dv >= 0 ? "" : "-") + "$" + Math.abs(dv).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " · " + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
      c.classList.toggle("dn", !up); setColor(up);
    }
    function drawChart(tf: string) {
      const n = TF[tf], s = Math.max(0, N - n); sliceStart = s;
      const fu = FUSD.slice(s); usd = fu;
      let mn = Math.min(...fu), mx = Math.max(...fu); const pad = (mx - mn) * 0.16 || 1; mn -= pad; mx += pad;
      const m = fu.length;
      const X = (i: number) => i / (m - 1) * W, Y = (v: number) => H - 14 - ((v - mn) / (mx - mn)) * (H - 28);
      fundPts = fu.map((v, i) => [X(i), Y(v)]);
      const fp = fundPts.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
      $("#fundpath")?.setAttribute("points", fp);
      $("#area")?.setAttribute("points", fp + " " + W + "," + H + " 0," + H);
      $("#axis").innerHTML = TFAX[tf].map((l) => "<span>" + l + "</span>").join("");
      r!.querySelectorAll(".tf").forEach((b: any) => b.classList.toggle("on", b.dataset.tf === tf));
      readout(m - 1);
    }
    const tfs = $("#tfs");
    const onTf = (e: any) => { const b = e.target.closest(".tf"); if (b) { e.stopPropagation(); drawChart(b.dataset.tf); } };
    tfs?.addEventListener("click", onTf);

    // ── scrub ──
    const stage = $("#stage"), sdot = $("#sdot"), stip = $("#stip");
    function scrub(cx: number) {
      const fr = $(".chartfield").getBoundingClientRect();
      const t = Math.min(1, Math.max(0, (cx - fr.left) / fr.width)), i = Math.round(t * (fundPts.length - 1)), p = fundPts[i];
      const px = p[0] / W * fr.width, py = p[1] / H * fr.height;
      sdot.style.left = px + "px"; sdot.style.top = py + "px";
      stip.style.left = Math.min(fr.width - 60, Math.max(60, px)) + "px"; stip.style.top = (py - 26) + "px";
      stip.textContent = DATES[sliceStart + i] || ""; stage.classList.add("scrubbing"); readout(i);
    }
    const unscrub = () => { stage.classList.remove("scrubbing"); if (usd.length) readout(usd.length - 1); };

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
      ? items.map((h) => `<div class="holdrow" data-vote="1"><span class="htk">${h.sym}</span><div class="hn"><b>${h.name || h.sym}</b><s>${h.sym}</s></div>${hold.length ? pnlCell(h.pnl) : `<span style="flex:1"></span>`}${hold.length ? `<span class="hwt tnum">$${Math.round(h.val || 0).toLocaleString()}</span>` : ""}</div>`).join("")
      : `<div style="color:var(--subtle);font:500 13px/1.6 var(--font-inter);padding:8px 2px">No holdings yet — once the basket runs, your positions show here. <a href="/vote" style="color:var(--teal)">Cast your vote →</a></div>`;

    drawChart("ALL"); renderSectors();
    // Count up the hero ONCE per visit. The snapshot now refreshes on a 5-minute
    // poll, and replaying a 0→$23,567 count-up every five minutes would read as a
    // glitch — drawChart() already wrote the new value, so later updates just land.
    if (!RM && !counted.current) {
      counted.current = true;
      const el = $("#val"); let t0: number | null = null;
      const cu = (ts: number) => { if (!t0) t0 = ts; const p = Math.min((ts - t0) / 950, 1), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(value * e); if (p < 1) requestAnimationFrame(cu); };
      requestAnimationFrame(cu);
    }

    // ── carousel ──
    const dots = [...r.querySelectorAll(".dots button")] as any[];
    let cur = 0;
    const go = (i: number) => { cur = (i + 3) % 3; stage.dataset.s = String(cur); dots.forEach((d, kk) => d.classList.toggle("on", kk === cur)); if (cur !== 1) unscrub(); };
    const next = () => go(cur + 1), prev = () => go(cur - 1);
    $("#next").addEventListener("click", (e: any) => { e.stopPropagation(); next(); });
    $("#prev").addEventListener("click", (e: any) => { e.stopPropagation(); prev(); });
    dots.forEach((d, kk) => d.addEventListener("click", (e: any) => { e.stopPropagation(); go(kk); }));
    const onKey = (e: KeyboardEvent) => { if (e.key === "ArrowRight") next(); else if (e.key === "ArrowLeft") prev(); };
    document.addEventListener("keydown", onKey);
    $("#donutring").addEventListener("click", (e: any) => e.stopPropagation());
    const onNav = (e: any) => { if (e.target.closest("[data-vote],.strip a")) router.push("/vote"); };
    r.addEventListener("click", onNav);

    // drag / wheel / touch
    let down = false, x0 = 0, moved = 0;
    const pd = (e: PointerEvent) => { if ((e.target as HTMLElement).closest(".arw,.dots,.strip a,.tf,.donutring,.leg,.scrolldn,a,button")) return; down = true; x0 = e.clientX; moved = 0; stage.classList.add("grab"); };
    const pm = (e: PointerEvent) => { if (down) { moved = e.clientX - x0; return; } if (cur === 1) { const fr = stage.getBoundingClientRect(); if (e.clientX >= fr.left && e.clientX <= fr.right && e.clientY >= fr.top && e.clientY <= fr.bottom) scrub(e.clientX); } };
    const pu = () => { if (!down) return; down = false; stage.classList.remove("grab"); if (Math.abs(moved) > 45) go(cur + (moved < 0 ? 1 : -1)); };
    stage.addEventListener("pointerdown", pd); window.addEventListener("pointermove", pm); window.addEventListener("pointerup", pu);
    const wlock = { v: false };
    const onWheel = (e: WheelEvent) => { if (Math.abs(e.deltaX) > Math.abs(e.deltaY) * 1.6 && Math.abs(e.deltaX) > 22) { e.preventDefault(); if (!wlock.v) { wlock.v = true; go(cur + (e.deltaX > 0 ? 1 : -1)); setTimeout(() => (wlock.v = false), 480); } } };
    stage.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      tfs?.removeEventListener("click", onTf);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("pointermove", pm); window.removeEventListener("pointerup", pu);
      stage.removeEventListener("wheel", onWheel as any);
    };
    // `loaded` matters: the stage only mounts after it flips, and that render can
    // have identical value/items deps — without it the effect would never
    // initialize the freshly-mounted stage.
  }, [loaded, value, pctTarget, itemsKey, itemsLabel, histKey, router]); // eslint-disable-line react-hooks/exhaustive-deps

  // Wait for the account + holdings fetches before choosing what to show — a
  // funded member should never flash the zero-state (or a $0 hero) while loading.
  if (!loaded) return null;

  if (value <= 0) {
    if (failed) {
      return (
        <div className="glass shine enter mt-4 p-6 sm:p-7">
          <div className="text-[13px] text-red">⚠ Couldn&apos;t load your account right now — refresh to try again.</div>
        </div>
      );
    }
    // Connected but nothing invested yet: a quiet zero-state instead of a dead
    // $0 cinematic stage (the effect above already bails when value <= 0).
    return (
      <div className="glass shine enter mt-4 p-6 sm:p-7">
        <div className="flex items-center gap-2.5">
          <span className="dot bg-teal pulse" />
          <h2 className="font-display text-[16px] font-bold">You&apos;re connected</h2>
        </div>
        <p className="mt-2 max-w-[58ch] text-[13px] leading-relaxed text-muted">
          Your account is connected — commit an amount in{" "}
          <Link href="/account" className="text-teal">Account</Link> and flip Participate on;
          your first basket buys on the next buy day.
        </p>
        {/* "nothing invested" is itself a money claim — date it. */}
        <Freshness asOf={accountAsOf} stale={accountStale} busy={accountRefreshing}
          onRefresh={refreshAccount} className="mt-3" />
      </div>
    );
  }

  return (
    <div className="cine" ref={root}>
      <section id="stage" data-s="0">
        <div className="chartfield">
          <svg id="chart" viewBox="0 0 1000 600" preserveAspectRatio="none">
            <defs>
              <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                <stop id="fc0" offset="0" stopColor="#2DD4BF" stopOpacity=".26" />
                <stop id="fc1" offset="1" stopColor="#2DD4BF" stopOpacity="0" />
              </linearGradient>
              <filter id="g" x="-4%" y="-20%" width="108%" height="140%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
            </defs>
            <polygon id="area" points="" fill="url(#fill)" />
            <polyline id="fundpath" points="" fill="none" stroke="#2DD4BF" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" filter="url(#g)" />
          </svg>
        </div>
        <div className="sdot" id="sdot" /><div className="stip" id="stip" />

        <div className="value">
          <div className="num tnum" id="val">{"$0.00"}</div>
          <div className="chg tnum" id="chg">▲ $0 · +0.00%</div>
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
          <div className="axis" id="axis" />
          <div className="tfs" id="tfs">
            {["1D","1W","1M","3M","1Y","ALL"].map((t) => <button key={t} className={`tf ${t === "ALL" ? "on" : ""}`} data-tf={t}>{t}</button>)}
          </div>
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
          <div className="label">Consensus basket · {session?.voters ?? 0} voter{(session?.voters ?? 0) === 1 ? "" : "s"} · by voting power</div>
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
