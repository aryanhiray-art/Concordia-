"use client";

// Step 2 — the wizard's compact Robinhood connect flow (the account page has
// its own richer version in ConnectCard.tsx; the start/paste/complete logic is
// mirrored, not shared). Robinhood's agentic OAuth only redirects to
// localhost, so the member approves in a new tab, dead-ends on a "can't
// connect" page, and pastes that page's URL back here. On success we refresh
// the member, show a small "Connected ✓" beat, and auto-advance.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ConnectExplainer } from "@/components/ConnectExplainer";
import { Button } from "@/components/ui";

export function StepConnect({ onDone }: { onDone: () => void }) {
  const { member, refreshMember } = useAuth();
  const rh = member?.robinhood;
  const connected = !!(rh?.status === "connected" && rh?.account_number);
  // completed the flow before but the tokens are gone (Robinhood rotates
  // credentials / member disconnected) — the same connect flow reattaches.
  const reauth = !!(rh?.account_number && !connected);

  const [phase, setPhase] = useState<"idle" | "paste">("idle");
  const [pasteUrl, setPasteUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Robinhood approvals sometimes expire before the paste (state expiry /
  // reused code) — after a failure we say so explicitly; redoing works.
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [justConnected, setJustConnected] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  async function connect() {
    setBusy(true); setErr(null);
    const tab = window.open("", "_blank"); // open synchronously (no popup block)
    try {
      const { authorize_url } = await api.rhStart();
      if (tab) tab.location.href = authorize_url; else window.location.href = authorize_url;
      setPhase("paste");
    } catch (e: any) {
      if (tab) tab.close();
      setErr(e?.message || "couldn't start Robinhood connect");
    } finally { setBusy(false); }
  }

  async function complete() {
    setBusy(true); setErr(null);
    // members paste with stray whitespace / wrapping quotes — clean before submit
    const url = pasteUrl.trim().replace(/^["'<]+|["'>]+$/g, "");
    try {
      await api.rhComplete(url);
      await refreshMember(); // flips member.robinhood to connected below
      setFailedAttempts(0);
      setJustConnected(true);
      timer.current = setTimeout(onDone, 1300); // the success beat, then advance
    } catch (e: any) {
      setFailedAttempts((n) => n + 1);
      setErr(e?.message || "couldn't finish connecting");
    } finally { setBusy(false); }
  }

  if (connected) {
    return (
      <div className="enter">
        <div className="flex items-center gap-3.5 rounded-[14px] border border-green/30 bg-green/[0.07] p-4">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-full bg-green/15 text-[16px] font-bold text-green" aria-hidden>✓</span>
          <div className="min-w-0">
            <div className="font-display text-[15px] font-semibold text-green">Connected ✓</div>
            <div className="mt-0.5 text-[12.5px] text-muted">
              Robinhood account <span className="font-display font-semibold text-ink">····{String(rh!.account_number).slice(-4)}</span>
            </div>
          </div>
        </div>
        {justConnected ? (
          <p className="mt-3 text-center text-[12.5px] text-subtle">One quick account check next…</p>
        ) : (
          <Button variant="go" className="mt-4 w-full" onClick={onDone}>Continue</Button>
        )}
      </div>
    );
  }

  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-muted">
        {reauth ? (
          "Robinhood signed this connection out (it rotates credentials periodically). Reconnect to resume — takes under a minute."
        ) : (
          <>You log in on Robinhood&apos;s own site and approve agentic trading —
          your credentials never touch Concordia.</>
        )}
      </p>

      {phase === "idle" ? (
        <>
          <ConnectExplainer />
          <Button variant="go" className="mt-4 w-full" onClick={connect} busy={busy}>
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
              strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
            {reauth ? "Reconnect Robinhood" : "Connect Robinhood"}
          </Button>
        </>
      ) : (
        <div className="enter mt-4 rounded-[14px] border border-teal/20 bg-teal/[0.04] p-4">
          <div className="label !text-teal mb-2">Almost there — one paste</div>
          <ol className="ml-4 list-decimal space-y-1 text-[12.5px] leading-relaxed text-muted">
            <li>In the Robinhood tab that opened, log in and <b className="text-ink">Approve</b>.</li>
            <li>It&rsquo;ll land on a <b className="text-ink">&ldquo;can&rsquo;t connect / localhost&rdquo;</b> page — that&rsquo;s expected.</li>
            <li>Copy that page&rsquo;s <b className="text-ink">full address-bar URL</b> and paste it below.</li>
          </ol>
          <input
            value={pasteUrl}
            onChange={(e) => { setPasteUrl(e.target.value); setErr(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && pasteUrl.trim() && !busy) complete(); }}
            placeholder="http://localhost:8917/callback?code=…&state=…"
            className="input mt-3 w-full font-mono !text-[12px]" spellCheck={false} autoComplete="off"
          />
          <div className="mt-3 flex items-center gap-2.5">
            <Button variant="ok" onClick={complete} busy={busy} disabled={!pasteUrl.trim()}>Finish connecting</Button>
            <button onClick={() => { setPhase("idle"); setErr(null); }}
              className="text-[12.5px] text-subtle hover:text-ink">cancel</button>
            <button onClick={connect}
              className="ml-auto text-[12px] text-subtle hover:text-teal">Re-open Robinhood</button>
          </div>
        </div>
      )}

      {err && (
        <div className="mt-3 text-[13px] leading-relaxed text-red">
          ⚠ {err}
          {failedAttempts >= 1 && (
            <div className="mt-1.5 text-[12.5px] text-amber">
              This happens — Robinhood approvals sometimes expire before the paste. Click
              &ldquo;Re-open Robinhood&rdquo; and approve again; the second attempt almost
              always works.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
