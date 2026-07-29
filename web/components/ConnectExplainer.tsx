"use client";

// Shown BEFORE the member clicks Connect. Robinhood's agentic OAuth can only
// redirect to localhost, so the flow *by design* dead-ends on a browser
// "can't connect" page whose URL carries the ?code=&state= we need — the member
// copies that URL back into Concordia. This looping visualizer sells the three
// steps up front so the dead-end page reads as expected, not broken.
// Pure CSS/SVG (no GIFs, no libraries). prefers-reduced-motion freezes it to a
// static step list (the animated stage is decorative and hidden).

// One 9s cycle, 3s per step. Chips + scenes share the cycle via negative
// animation-delays (-9s/-6s/-3s) so nothing flashes "active" on mount.
const css = `
.rhx-row{display:flex;gap:14px;align-items:center}
.rhx-steps{flex:1;min-width:0;margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px}
.rhx-chip{display:flex;align-items:flex-start;gap:9px;padding:8px 11px;border-radius:11px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.02);color:var(--subtle);font:500 12.5px/1.45 var(--font-inter),sans-serif;animation:rhx-chip 9s infinite}
.rhx-chip b{font-weight:700}
.rhx-chip:nth-child(1){animation-delay:-9s}
.rhx-chip:nth-child(2){animation-delay:-6s}
.rhx-chip:nth-child(3){animation-delay:-3s}
.rhx-n{flex:none;width:18px;height:18px;margin-top:-1px;border-radius:50%;display:grid;place-items:center;border:1px solid currentColor;font:700 10.5px/1 var(--font-outfit),sans-serif}
@keyframes rhx-chip{0%,29%{color:var(--ink);border-color:rgba(45,212,191,.35);background:rgba(45,212,191,.07)}33%,97%{color:var(--subtle);border-color:rgba(255,255,255,.07);background:rgba(255,255,255,.02)}100%{color:var(--ink);border-color:rgba(45,212,191,.35);background:rgba(45,212,191,.07)}}
.rhx-stage{position:relative;flex:none;width:176px;height:112px}
.rhx-scene{position:absolute;inset:0;display:grid;place-items:center;opacity:0;animation:rhx-scene 9s infinite}
.rhx-scene:nth-child(1){animation-delay:-9s}
.rhx-scene:nth-child(2){animation-delay:-6s}
.rhx-scene:nth-child(3){animation-delay:-3s}
@keyframes rhx-scene{0%,29%{opacity:1;transform:none}33%,96%{opacity:0;transform:translateY(5px)}100%{opacity:1;transform:none}}
.rhx-check{stroke-dasharray:24;stroke-dashoffset:24;animation:rhx-draw 9s infinite}
@keyframes rhx-draw{0%,2%{stroke-dashoffset:24}9%,100%{stroke-dashoffset:0}}
.rhx-addr{fill-opacity:.08;stroke-opacity:.45;animation:rhx-pulse 1.5s ease-in-out infinite}
@keyframes rhx-pulse{0%,100%{stroke-opacity:.3;fill-opacity:.05}50%{stroke-opacity:1;fill-opacity:.16}}
.rhx-flow{stroke-dasharray:3 4;animation:rhx-dash .9s linear infinite}
@keyframes rhx-dash{to{stroke-dashoffset:-14}}
.rhx-fly{animation:rhx-fly 2.6s ease-in-out infinite}
@keyframes rhx-fly{0%,14%{transform:translateY(0);opacity:0}24%{opacity:1}70%{transform:translateY(58px);opacity:1}82%,100%{transform:translateY(58px);opacity:0}}
@media (max-width:560px){.rhx-row{flex-direction:column-reverse;align-items:stretch}.rhx-stage{width:100%}}
@media (prefers-reduced-motion:reduce){
.rhx-chip,.rhx-scene,.rhx-check,.rhx-addr,.rhx-flow,.rhx-fly{animation:none!important}
.rhx-stage{display:none}
.rhx-chip{color:var(--muted)}
}
`;

// shared mini browser chrome for scenes 1–2 (window + traffic dots + address bar)
function Chrome({ bar, children }: { bar?: React.ReactNode; children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 150 96" width="164" height="105" fill="none" aria-hidden>
      <rect x="1.5" y="1.5" width="147" height="93" rx="10" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.1)" />
      <line x1="1.5" y1="22" x2="148.5" y2="22" stroke="rgba(255,255,255,0.1)" />
      <circle cx="12" cy="12" r="2.2" fill="rgba(255,255,255,0.18)" />
      <circle cx="21" cy="12" r="2.2" fill="rgba(255,255,255,0.18)" />
      <circle cx="30" cy="12" r="2.2" fill="rgba(255,255,255,0.18)" />
      {bar ?? <rect x="40" y="6.5" width="100" height="11" rx="5.5" fill="rgba(255,255,255,0.06)" />}
      {children}
    </svg>
  );
}

export function ConnectExplainer() {
  return (
    <div className="mt-4 rounded-[14px] border border-white/10 bg-white/[0.03] p-4">
      <style>{css}</style>
      <div className="label mb-3">How connecting works</div>
      <div className="rhx-row">
        <ol className="rhx-steps">
          <li className="rhx-chip">
            <span className="rhx-n">1</span>
            <span>Approve on Robinhood.</span>
          </li>
          <li className="rhx-chip">
            <span className="rhx-n">2</span>
            <span>You&apos;ll land on a &ldquo;Can&apos;t connect&rdquo; page — <b>that&apos;s expected</b>.</span>
          </li>
          <li className="rhx-chip">
            <span className="rhx-n">3</span>
            <span>Copy that page&apos;s URL and paste it here.</span>
          </li>
        </ol>

        <div className="rhx-stage" aria-hidden="true">
          {/* scene 1 — approve on Robinhood: browser card, RH-green check draws in */}
          <div className="rhx-scene">
            <Chrome>
              <rect x="45" y="34" width="60" height="4" rx="2" fill="rgba(255,255,255,0.09)" />
              <rect x="55" y="42" width="40" height="4" rx="2" fill="rgba(255,255,255,0.09)" />
              <circle cx="75" cy="67" r="15" fill="rgba(0,200,5,0.1)" stroke="#00C805" strokeWidth="1.5" />
              <path className="rhx-check" d="M68.5 67.5 l5 5 l11 -11" stroke="#00C805" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </Chrome>
          </div>

          {/* scene 2 — the expected "can't connect" page: broken-page glyph, address bar pulses */}
          <div className="rhx-scene">
            <Chrome
              bar={
                <>
                  <rect className="rhx-addr" x="40" y="6.5" width="100" height="11" rx="5.5" fill="#2DD4BF" stroke="#2DD4BF" strokeWidth="1.3" />
                  <rect x="47" y="10" width="56" height="3.5" rx="1.75" fill="rgba(255,255,255,0.35)" />
                </>
              }
            >
              <circle cx="66" cy="50" r="2.4" fill="#9AA7B4" />
              <circle cx="84" cy="50" r="2.4" fill="#9AA7B4" />
              <path d="M66 64 q9 -8 18 0" stroke="#9AA7B4" strokeWidth="2" strokeLinecap="round" />
              <rect x="53" y="73" width="44" height="4" rx="2" fill="rgba(255,255,255,0.08)" />
            </Chrome>
          </div>

          {/* scene 3 — copy the URL, paste it here: address bar → paste field, animated arrow */}
          <div className="rhx-scene">
            <svg viewBox="0 0 150 96" width="164" height="105" fill="none" aria-hidden>
              <rect x="22" y="6" width="106" height="14" rx="7" fill="rgba(255,255,255,0.05)" stroke="rgba(45,212,191,0.45)" />
              <rect x="30" y="11" width="62" height="4" rx="2" fill="rgba(45,212,191,0.5)" />
              <rect x="107" y="9" width="6.5" height="7.5" rx="1.5" stroke="rgba(45,212,191,0.7)" strokeWidth="1.2" />
              <rect x="110" y="6.5" width="6.5" height="7.5" rx="1.5" stroke="rgba(45,212,191,0.4)" strokeWidth="1.2" />
              <line className="rhx-flow" x1="75" y1="27" x2="75" y2="52" stroke="#2DD4BF" strokeWidth="2" strokeLinecap="round" />
              <path d="M69.5 50.5 L75 56.5 L80.5 50.5" stroke="#2DD4BF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              <rect x="22" y="64" width="106" height="20" rx="8" fill="rgba(45,212,191,0.05)" stroke="rgba(45,212,191,0.55)" strokeDasharray="5 4" />
              <g className="rhx-fly">
                <rect x="55" y="8" width="40" height="10" rx="5" fill="rgba(45,212,191,0.16)" stroke="rgba(45,212,191,0.6)" />
                <rect x="61" y="11.5" width="28" height="3" rx="1.5" fill="rgba(45,212,191,0.7)" />
              </g>
            </svg>
          </div>
        </div>
      </div>

      <p className="mt-3 border-t border-white/[0.07] pt-3 text-[12px] leading-relaxed text-subtle">
        Heads up: Robinhood sometimes needs two tries — if pasting fails, click Connect
        and redo. Under a minute.
      </p>
    </div>
  );
}
