"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Account } from "@/lib/types";

// A representative market shape; we compress its daily returns to the member's
// real club P&L and scale it to end at their real club value.
const SPY = [680.73,678.87,671.40,676.47,680.59,684.83,687.96,690.38,690.31,687.85,687.01,681.92,683.17,687.72,691.81,689.58,689.51,694.07,695.16,693.77,690.36,692.24,691.66,677.58,685.40,688.98,689.23,692.73,695.49,695.42,694.04,691.97,695.41,689.53,686.19,677.62,690.62,693.95,692.12,691.96,681.27,681.75,682.85,686.29,684.48,689.43,682.39,687.35,693.15,689.30,685.99,686.38,680.33,685.13,681.31,672.38,678.27,677.18,676.33,666.06,662.29,669.03,670.79,661.43,659.80,648.57,655.38,653.18,656.82,645.09,650.34,655.24,655.83,658.93,659.22,676.01,679.91,679.46,686.10,694.46,699.94,701.66,710.14,708.72,711.21,713.94,715.17,718.66,720.65,723.77,733.83,737.62,739.30,742.31,748.17,738.65,741.25,745.64,750.59,754.60,756.48,759.57,757.09,737.55,741.75];
const COLORS = ["#2DD4BF","#22D3EE","#60A5FA","#818CF8","#A78BFA","#475569"];

export function CinematicOverview() {
  const { session } = useAuth();
  const router = useRouter();
  const root = useRef<HTMLDivElement>(null);
  const [acc, setAcc] = useState<Account | null>(null);

  useEffect(() => { api.account().then(setAcc).catch(() => {}); }, []);

  const pnl = acc?.pnl;
  const value = (pnl && pnl.invested > 0 ? pnl.value : null)
    ?? acc?.allocation?.resolved_usd
    ?? acc?.allocation?.balance?.total_value ?? 0;
  const pctTarget = (pnl && pnl.invested > 0 && pnl.profit_pct != null) ? pnl.profit_pct : 0;
  const basket = ((session?.picks?.length ? session.picks : session?.tally) || []).slice(0, 8);
  const basketKey = basket.map((b) => `${b.symbol}:${b.weight}`).join(",");
  const closesAt = session?.closes_at ?? 0;
  const open = session?.status === "open";

  useEffect(() => {
    const r = root.current;
    if (!r || value <= 0) return;
    const RM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const $ = (sel: string) => r.querySelector(sel) as any;
    const W = 1000, H = 600;

    // ── build the value series: compress SPY daily returns to the real P&L ──
    const N = SPY.length;
    const spyTot = SPY[N - 1] / SPY[0] - 1;
    const k = spyTot !== 0 ? (pctTarget / 100) / spyTot : 0;
    const fund = [1];
    for (let i = 1; i < N; i++) fund.push(fund[i - 1] * (1 + (SPY[i] / SPY[i - 1] - 1) * (k || 0.18)));
    const fsc = value / fund[N - 1];
    const FUSD = fund.map((v) => v * fsc);
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

    // ── donut: the basket allocation ──
    const totW = basket.reduce((a, b) => a + (b.weight || 0), 0) || 1;
    const slices = basket.slice(0, 5).map((b, i) => ({ nm: b.symbol, co: b.name, pc: Math.round((b.weight || 0) / totW * 1000) / 10, c: COLORS[i] }));
    const restPc = Math.round((100 - slices.reduce((a, s) => a + s.pc, 0)) * 10) / 10;
    if (restPc > 0.5 && basket.length > 5) slices.push({ nm: "Rest", co: `${basket.length - 5} more`, pc: restPc, c: COLORS[5] });
    const CC = 2 * Math.PI * 80, arcsEl = $("#arcs");
    function drawDonut() {
      while (arcsEl.children.length < 6) arcsEl.insertAdjacentHTML("beforeend", `<circle class="arc" cx="100" cy="100" r="80" stroke="#2DD4BF" stroke-dasharray="0 ${CC.toFixed(2)}"/>`);
      let off = 0;
      for (let i = 0; i < 6; i++) { const c = arcsEl.children[i];
        if (i < slices.length) { const len = slices[i].pc / 100 * CC; c.setAttribute("stroke", slices[i].c); c.setAttribute("stroke-dasharray", len.toFixed(2) + " " + (CC - len).toFixed(2)); c.setAttribute("stroke-dashoffset", (-off).toFixed(2)); off += len; }
        else c.setAttribute("stroke-dasharray", "0 " + CC.toFixed(2));
      }
      const leg = $("#leg"); leg.style.opacity = "0";
      setTimeout(() => { leg.innerHTML = slices.map((x) => `<div class="row"><span class="sw" style="background:${x.c};color:${x.c}"></span><span class="nm">${x.nm}<s>${x.co || ""}</s></span><span class="pc tnum">${x.pc}%</span></div>`).join(""); leg.style.opacity = "1"; }, 160);
      $("#dctr").textContent = String(basket.length); $("#dctrs").textContent = "names";
    }

    // ── holdings ──
    const spark = (up: boolean) => `<svg class="hspark" viewBox="0 0 300 38" preserveAspectRatio="none"><polyline points="${up ? "0,30 60,20 120,24 180,11 240,15 300,3" : "0,11 60,16 120,9 180,20 240,17 300,26"}" fill="none" stroke="${up ? "#34E3A8" : "#FF6B6B"}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    $("#holdings").innerHTML = basket.map((h, i) => `<div class="holdrow" data-vote="1"><span class="htk">${h.symbol}</span><div class="hn"><b>${h.name || h.symbol}</b><s>${h.symbol}</s></div>${spark(i % 3 !== 1)}<span class="hwt tnum">$${Math.round(h.weight || 0).toLocaleString()}</span></div>`).join("");

    drawChart("ALL"); drawDonut();
    // count-up the hero
    if (!RM) { const el = $("#val"); let t0: number | null = null; const cu = (ts: number) => { if (!t0) t0 = ts; const p = Math.min((ts - t0) / 950, 1), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(value * e); if (p < 1) requestAnimationFrame(cu); }; requestAnimationFrame(cu); }

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
    $("#ring").addEventListener("click", (e: any) => e.stopPropagation());
    const onNav = (e: any) => { if (e.target.closest("[data-vote],.strip a")) router.push("/vote"); };
    r.addEventListener("click", onNav);

    // drag / wheel / touch
    let down = false, x0 = 0, moved = 0;
    const pd = (e: PointerEvent) => { if ((e.target as HTMLElement).closest(".arw,.dots,.strip a,.tf,.ring,.leg,.scrolldn,a,button")) return; down = true; x0 = e.clientX; moved = 0; stage.classList.add("grab"); };
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
  }, [value, pctTarget, basketKey, router]); // eslint-disable-line react-hooks/exhaustive-deps

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
          <div className="ring" id="ring"><svg viewBox="0 0 200 200"><g id="arcs" /></svg><div className="ctr"><b id="dctr">0</b><span id="dctrs">names</span></div></div>
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
        <div className="label mb-3">Consensus basket · {session?.voters ?? 0} voter{(session?.voters ?? 0) === 1 ? "" : "s"} · by voting power</div>
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
