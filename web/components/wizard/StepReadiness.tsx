"use client";

// Step 4 — "One quick check": can the connected account actually
// place buys? Ready → success beat, auto-advance (only when this is the
// member's furthest step — revisits via Back get a manual Continue instead,
// so Back navigation is never fought by an auto-advance). The common blocker
// is Robinhood's investing-goals questionnaire, which gets its own
// walkthrough + a force re-check. No idle cash counts as ready (fully
// invested is the executor's steady state) with a quiet deposit note.
// Anything else: reason + Re-check + a quiet skip so nobody is ever
// trapped here.
//
// The step also owns the LIMITED-MARGIN gate: a cash agentic account waits
// T+1 for sale proceeds to settle, so its redeploys run a day behind. When
// the account is cash, auto-advance is suspended until the member either
// switches to limited margin (one click into Robinhood's self-serve flow —
// unsettled-funds trading, no borrowing, no leverage) or explicitly
// acknowledges the delay. A failed probe never traps: the gate fails open.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, Dots } from "@/components/ui";

type Readiness = { connected: boolean; ready: boolean; reason: string; detail?: string };
type MarginStatus = {
  connected: boolean; account_type?: string | null; delayed?: boolean | null;
  unsettled_funds?: string | null; acknowledged?: boolean;
  upgrade?: { web_upgrade_url: string; mobile_upgrade_url: string; upgrade_fallback: string } | null;
};

export function StepReadiness({ onDone, auto }: { onDone: () => void; auto: boolean }) {
  const [res, setRes] = useState<Readiness | null>(null);
  const [margin, setMargin] = useState<MarginStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [recheckingMargin, setRecheckingMargin] = useState(false);
  const [keepCash, setKeepCash] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  // The margin gate: cash + not yet acknowledged blocks auto-advance.
  // Unknown type / probe error / not-yet-loaded margin=null fails OPEN once
  // readiness itself is green (never trap a member on a telemetry hiccup) —
  // except we hold the auto-advance until the margin probe has answered at
  // least once, so the upgrade offer isn't skipped by the 1.5s timer.
  const marginGateOpen =
    !!margin &&
    (margin.account_type !== "cash" || margin.acknowledged === true);

  const check = useCallback(async (force: boolean) => {
    setChecking(true); setErr(null);
    try {
      const r = await api.tradeReadiness(force);
      setRes(r);
      let m: MarginStatus | null = null;
      if (r.ready) {
        try { m = await api.marginStatus(force); setMargin(m); }
        catch { m = { connected: true }; setMargin(m); }
      }
      const gateOpen = !!m && (m.account_type !== "cash" || m.acknowledged === true);
      if (r.ready && auto && gateOpen) timer.current = setTimeout(onDone, 1500);
    } catch (e: any) {
      setErr(e?.message || "couldn't reach Robinhood to check");
    } finally { setChecking(false); }
  }, [auto, onDone]);

  // first look uses the cached readiness; Re-check forces a fresh one
  useEffect(() => { check(false); }, [check]);

  const recheckMargin = useCallback(async () => {
    setRecheckingMargin(true);
    try { setMargin(await api.marginStatus(true)); }
    catch { /* keep the previous state — fail open, not trapped */ }
    finally { setRecheckingMargin(false); }
  }, []);

  const acknowledgeCash = useCallback(async () => {
    try { await api.marginAck(true); } catch { /* non-fatal */ }
    setMargin((m) => (m ? { ...m, acknowledged: true } : m));
    onDone();
  }, [onDone]);

  if (checking && !res && !err) {
    return (
      <div className="flex items-center gap-3 py-6 text-[13px] text-muted">
        <Dots /> Checking your account with Robinhood…
      </div>
    );
  }

  if (res?.ready) {
    const isCash = margin?.account_type === "cash";
    const marginOk = margin && !isCash && !!margin.account_type;
    return (
      <div className="enter">
        <div className="flex items-center gap-3.5 rounded-[14px] border border-green/30 bg-green/[0.07] p-4">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-full bg-green/15 text-[16px] font-bold text-green" aria-hidden>✓</span>
          <div className="font-display text-[15px] font-semibold text-green">Your account is clear to trade ✓</div>
        </div>
        {res.reason === "fully_invested" && (
          <p className="mt-3 text-[12.5px] leading-relaxed text-subtle">
            No idle cash right now — you&rsquo;re fully invested. The club trades with settled
            cash, so deposit whenever you want it doing more.
          </p>
        )}

        {marginOk && (
          <p className="mt-3 text-[12.5px] leading-relaxed text-subtle">
            <b className="text-green">Limited margin ✓</b> — your sale proceeds redeploy
            immediately; no settlement delays.
          </p>
        )}

        {isCash && !margin?.acknowledged && !keepCash && (
          <div className="mt-4 rounded-[14px] border border-accent/40 bg-accent/[0.06] p-4">
            <div className="font-display text-[14.5px] font-semibold text-ink">
              One click makes your trades instant
            </div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
              Your agentic account is a <b className="text-ink">cash account</b>: when the club
              sells, the proceeds settle the next business day before they can buy again — your
              redeploys run <b className="text-ink">a day behind</b>. Robinhood&rsquo;s{" "}
              <b className="text-ink">limited margin</b> removes exactly that delay: trade with
              unsettled funds, <b className="text-ink">no borrowing, no leverage</b>, free. You
              approve it on Robinhood&rsquo;s own site.
            </p>
            {margin?.upgrade && (
              <>
                <Button
                  variant="go" className="mt-3 w-full"
                  onClick={() => window.open(margin.upgrade!.web_upgrade_url, "_blank", "noopener")}
                >
                  Switch to limited margin on Robinhood →
                </Button>
                <div className="mt-1.5 text-center text-[11.5px] text-subtle">
                  on your phone?{" "}
                  <a className="underline hover:text-muted" target="_blank" rel="noopener noreferrer"
                     href={margin.upgrade.mobile_upgrade_url}>
                    open in the Robinhood app
                  </a>
                </div>
              </>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant="ok" onClick={recheckMargin} busy={recheckingMargin}>
                I switched — re-check
              </Button>
              <button
                onClick={() => setKeepCash(true)}
                className="text-[12px] text-subtle transition hover:text-muted"
              >
                keep my cash account →
              </button>
            </div>
          </div>
        )}

        {isCash && !margin?.acknowledged && keepCash && (
          <div className="mt-4 rounded-[14px] border border-gold/40 bg-gold/[0.07] p-4">
            <div className="font-display text-[14.5px] font-semibold text-ink">
              Staying on cash — your trades will be delayed
            </div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
              On a cash account, sale proceeds settle the next business day (T+1) before the
              club can redeploy them — so after every sell day, your buys land a day behind
              members on limited margin. Nothing breaks; it&rsquo;s just slower. You can switch
              to limited margin any time from your Account page.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant="ok" onClick={acknowledgeCash}>
                I understand my trades will be delayed — continue
              </Button>
              <button
                onClick={() => setKeepCash(false)}
                className="text-[12px] text-subtle transition hover:text-muted"
              >
                ← actually, show me the switch
              </button>
            </div>
          </div>
        )}

        {isCash && margin?.acknowledged && (
          <p className="mt-3 text-[12.5px] leading-relaxed text-subtle">
            <b className="text-gold">Cash account</b> — trades delayed by T+1 settlement (you
            acknowledged this). Switch to limited margin anytime from your Account page.
          </p>
        )}

        {marginGateOpen || !margin || !isCash ? (
          auto && marginGateOpen ? (
            <p className="mt-3 text-center text-[12.5px] text-subtle">Next: commit your stake…</p>
          ) : marginGateOpen ? (
            <Button variant="go" className="mt-4 w-full" onClick={onDone}>Continue</Button>
          ) : !margin && !checking ? (
            <Button variant="go" className="mt-4 w-full" onClick={onDone}>Continue</Button>
          ) : null
        ) : null}
      </div>
    );
  }

  if (res?.reason === "needs_questionnaire") {
    return (
      <div>
        <p className="text-[13.5px] leading-relaxed text-muted">
          Robinhood needs a few <b className="text-ink">investing-goals answers</b> before
          it allows buys. One-time, about two minutes:
        </p>
        <ol className="ml-4 mt-3 list-decimal space-y-1.5 text-[13px] leading-relaxed text-muted">
          <li>Open the <b className="text-ink">Robinhood app</b>.</li>
          <li>Go to <b className="text-ink">Settings → Investing profile</b> (or start a $1 buy — it&rsquo;ll prompt you).</li>
          <li>Answer the questions, then come back here.</li>
        </ol>
        <Button variant="ok" className="mt-4" onClick={() => check(true)} busy={checking}>Re-check</Button>
      </div>
    );
  }

  // anything else (including a failed fetch): reason + Re-check + quiet skip
  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-red">
        ⚠ {err ?? `That check didn't pass yet — Robinhood says “${(res?.reason || "unknown").replace(/_/g, " ")}”.`}
      </p>
      {res?.detail && <p className="mt-1.5 text-[12.5px] leading-relaxed text-subtle">{res.detail}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="ok" onClick={() => check(true)} busy={checking}>Re-check</Button>
        <button onClick={onDone} className="text-[12px] text-subtle transition hover:text-muted">
          skip this check for now →
        </button>
      </div>
    </div>
  );
}
