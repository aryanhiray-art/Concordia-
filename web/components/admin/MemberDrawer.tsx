"use client";

// One member, everything about them: live balances + holdings (best-effort
// broker reads), power history, today's picks and order results, and the
// admin-mediated password reset. Bots get their strategy/model and thesis
// instead of the reset action (no login to reset).
//
// The overlay is createPortal'd to document.body ON PURPOSE: in-page fixed
// overlays break under ancestor transforms (the app's animations create
// containing blocks), so position:fixed only behaves from the body.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type AdminMemberDetail } from "@/lib/api";
import { Button, Pill, Stat } from "@/components/ui";
import { fmtTradeDate } from "@/lib/format";
import { SectionLabel, TH_FONT as TH, execTone, isDeferred, money } from "./util";

function CopyChip({ value }: { value: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  return (
    <button type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value).then(
          () => setState("done"),
          () => setState("failed"),
        );
        setTimeout(() => setState("idle"), 1800);
      }}
      className="text-[11.5px] font-semibold text-teal hover:underline">
      {state === "done" ? "copied" : state === "failed" ? "select + copy manually" : "copy"}
    </button>
  );
}

function Delta({ d }: { d?: number | null }) {
  if (d == null || d === 0) return <span className="text-subtle">—</span>;
  const up = d >= 0;
  return <span className={up ? "text-green" : "text-red"}>{up ? "▲" : "▼"} {money(Math.abs(d))}</span>;
}

export function MemberDrawer({ memberId, onClose }: { memberId: number; onClose: () => void }) {
  const [data, setData] = useState<AdminMemberDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reset, setReset] = useState<{ ok: boolean; email: string; temp_password: string } | null>(null);
  const [resetErr, setResetErr] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setData(null); setErr(null); setReset(null); setResetErr(null);
    api.adminMember(memberId)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "couldn't load member"); });
    return () => { alive = false; };
  }, [memberId]);

  // Esc closes; Tab is trapped inside the dialog — without the trap, tabbing
  // walks focus into the console behind the backdrop while the drawer is modal
  // (the same dialog contract as StockPanel — one convention for slide-overs).
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

  // Take focus on open, hand it back on close; lock page scroll. Mount-only ON
  // PURPOSE (StockPanel's split): keyed on [onClose] this re-ran on every 60s
  // poll re-render of the console — re-stealing focus, flickering the scroll
  // lock, and returning focus to whatever happened to be active mid-poll.
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

  const m = data?.member;
  const isBot = !!(m?.is_bot);
  const hist = data?.power?.history ?? [];
  const picks = data?.picks ?? [];
  const execs = data?.executions ?? [];
  const holdings = data?.holdings ?? [];

  async function doReset() {
    if (!m) return;
    if (!window.confirm(`Reset ${m.display_name}'s password? Their current sessions will be logged out.`)) return;
    setResetting(true); setResetErr(null);
    try {
      setReset(await api.adminResetPassword(memberId));
    } catch (e: any) {
      setResetErr(e?.message || "reset failed");
    } finally { setResetting(false); }
  }

  return createPortal(
    <>
      <div className="sbackdrop" onClick={onClose} aria-hidden />
      <div ref={panelRef} className="spanel" role="dialog" aria-modal="true"
        aria-label={m?.display_name ?? "Member detail"} tabIndex={-1}>
        <div className="flex min-h-full flex-col p-5 sm:p-6">
          {/* header: identity + close, always present */}
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2.5">
                <h2 className="font-display text-[20px] font-bold leading-none tracking-tight">
                  {m?.display_name ?? `member #${memberId}`}
                </h2>
                {m && (isBot
                  ? <Pill tone="neutral">bot</Pill>
                  : m.role !== "member" && <Pill tone="teal">{m.role}</Pill>)}
              </div>
              {m && (
                <div className="mt-1.5 truncate text-[12.5px] text-muted">
                  {isBot
                    ? <>{m.bot_strategy || "bot"} · <span className="font-mono text-[11.5px]">{m.bot_model || "?"}</span></>
                    : m.email}
                </div>
              )}
            </div>
            <button onClick={onClose} aria-label="Close"
              className="grid h-8 w-8 flex-none place-items-center rounded-full border border-hair text-muted transition hover:border-white/25 hover:text-ink">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>

          <div className="mt-5 flex-1 space-y-5">
            {err && (
              <div className="rounded-[14px] border border-red/30 bg-red/[0.07] p-4 text-[13px] text-red">
                ⚠ {err}
              </div>
            )}
            {!data && !err && <div className="py-16 text-center text-[13px] text-subtle">loading member…</div>}

            {data && (
              <>
                {/* password recovery — humans only; the temp password shows ONCE */}
                {!isBot && (
                  <div>
                    <Button variant="glass" busy={resetting} onClick={doReset}
                      className="!px-3.5 !py-2 !text-[12.5px]">
                      Reset password
                    </Button>
                    {reset && (
                      <div className="mt-2.5 rounded-[12px] border border-teal/30 bg-teal/[0.06] p-3 text-[12.5px] leading-relaxed text-muted">
                        Temp password for <b className="text-ink">{reset.email}</b> — share once, they change it after login:
                        <div className="mt-1 flex items-center gap-2.5">
                          <span className="font-mono text-[13.5px] font-semibold text-ink">{reset.temp_password}</span>
                          <CopyChip value={reset.temp_password} />
                        </div>
                      </div>
                    )}
                    {resetErr && <div className="mt-2 text-[12.5px] text-red">⚠ {resetErr}</div>}
                  </div>
                )}

                {isBot && data.bot_thesis && (
                  <div className="rounded-[12px] border border-hair bg-white/[0.03] p-3">
                    <SectionLabel>Today&apos;s thesis</SectionLabel>
                    <p className="text-[13px] italic leading-relaxed text-muted">&ldquo;{data.bot_thesis}&rdquo;</p>
                  </div>
                )}

                {/* balances — live broker read when available */}
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  <Stat label="Voting power" value={money(data.power?.power)} tone="teal"
                    sub={<Delta d={data.power?.delta} />} />
                  <Stat label="Account value" value={money(data.portfolio?.total_value)} />
                  <Stat label="Buying power" value={money(data.portfolio?.buying_power)} />
                </div>

                {hist.length > 1 && (
                  <div>
                    <SectionLabel>Voting power (weekly)</SectionLabel>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11.5px]">
                      {hist.map((h) => (
                        <span key={h.week_start} className="text-subtle tnum">
                          {h.week_start.slice(5)}<span className="ml-1 text-ink">{money(h.power)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {picks.length > 0 && (
                  <div>
                    <SectionLabel>
                      Picks{data.session_date ? ` — ${fmtTradeDate(data.session_date)}` : ""} ({picks.length})
                    </SectionLabel>
                    <div className="flex flex-wrap gap-1.5">
                      {picks.map((p) => (
                        <span key={p.symbol}
                          className="rounded-[10px] border border-teal/30 bg-teal/[0.08] px-2.5 py-1 font-mono text-[12px]">
                          {p.symbol}<span className="ml-1.5 text-teal tnum">{p.weight}%</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {execs.length > 0 && (
                  <div>
                    <SectionLabel>Orders this session</SectionLabel>
                    <div className="space-y-1.5">
                      {execs.map((e, i) => (
                        <div key={i} className="flex flex-wrap items-center gap-2 text-[12.5px]">
                          <span className="w-14 font-mono font-semibold">{e.symbol}</span>
                          <span className="text-subtle">{e.side || "—"}</span>
                          <span className="text-muted tnum">
                            {e.dollar_amount != null ? money(e.dollar_amount) : "—"}
                          </span>
                          <Pill tone={execTone(e.status, e.error)}>
                            {isDeferred(e.error) ? "deferred" : e.status}
                          </Pill>
                          {e.error && !isDeferred(e.error) && (
                            <span className="text-[11.5px] text-subtle">{e.error}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div>
                  <SectionLabel>Holdings</SectionLabel>
                  {data.rh_error ? (
                    <div className="text-[12.5px] text-amber">{data.rh_error}</div>
                  ) : holdings.length === 0 ? (
                    <div className="text-[12.5px] text-subtle">
                      {isBot ? "bots hold no positions" : "no long positions (or no broker read yet)"}
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[420px] text-[12.5px]">
                        <thead>
                          {/* NOT .label on <th>: its display:block breaks table-cell layout */}
                          <tr>
                            <th className={`${TH} pr-3 text-left`}>symbol</th>
                            <th className={`${TH} text-right`}>qty</th>
                            <th className={`${TH} text-right`}>avg</th>
                            <th className={`${TH} text-right`}>last</th>
                            <th className={`${TH} text-right`}>value</th>
                            <th className={`${TH} text-right`}>P&amp;L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {holdings.map((h) => (
                            <tr key={h.symbol} className="border-t border-hair">
                              <td className="py-2 pr-3 font-mono font-semibold">{h.symbol}</td>
                              <td className="py-2 text-right text-muted tnum">{h.quantity}</td>
                              <td className="py-2 text-right text-muted tnum">{money(h.avg_buy_price)}</td>
                              <td className="py-2 text-right text-muted tnum">{h.last != null ? money(h.last) : "—"}</td>
                              <td className="py-2 text-right tnum">{money(h.market_value)}</td>
                              <td className={`py-2 text-right tnum ${h.pnl == null ? "text-subtle" : h.pnl >= 0 ? "text-green" : "text-red"}`}>
                                {h.pnl != null ? money(h.pnl) : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {data.member.robinhood?.status && (
                  <p className="text-[11.5px] text-subtle">
                    robinhood: {data.member.robinhood.status}
                    {data.member.robinhood.account_number ? ` · acct ${data.member.robinhood.account_number}` : ""}
                    {data.power?.as_of_week ? ` · power as of week ${data.power.as_of_week}` : ""}
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
