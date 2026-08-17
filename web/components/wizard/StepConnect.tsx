"use client";

// Step 2 — approve on Robinhood. ONE action: the Connect button opens
// Robinhood in a new tab and the wizard advances to the paste step (step 3),
// which owns the paste-back half. Splitting the old three-instruction panel
// across two screens keeps each one inside a laptop viewport.
// (The account page keeps a richer combined version in ConnectCard.tsx for
// the reconnect-after-token-rotation case; the logic is mirrored, not shared.)

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui";
import { ConnectExplainer } from "@/components/ConnectExplainer";

export function StepConnect({ onOpened, onConnected }: { onOpened: () => void; onConnected: () => void }) {
  const { member } = useAuth();
  const rh = member?.robinhood;
  const connected = !!(rh?.status === "connected" && rh?.account_number);
  // completed the flow before but the tokens are gone (Robinhood rotates
  // credentials / member disconnected) — the same connect flow reattaches.
  const reauth = !!(rh?.account_number && !connected);

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function connect() {
    setBusy(true); setErr(null);
    const tab = window.open("", "_blank"); // open synchronously (no popup block)
    try {
      const { authorize_url } = await api.rhStart();
      try { sessionStorage.setItem("concordia_oauth_inflight", "1"); } catch {}
      if (tab) tab.location.href = authorize_url; else window.location.href = authorize_url;
      onOpened(); // the paste step takes it from here
    } catch (e: any) {
      if (tab) tab.close();
      setErr(e?.message || "couldn't start Robinhood connect");
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
        <Button variant="go" className="mt-4 w-full" onClick={onConnected}>Continue</Button>
      </div>
    );
  }

  return (
    <div>
      <p className="text-[13.5px] leading-relaxed text-muted">
        {reauth ? (
          "Robinhood rotates credentials periodically — reconnect to resume. Under a minute."
        ) : (
          <>You log in and approve on <b className="text-ink">Robinhood&apos;s own site</b> —
          your credentials never touch Concordia.</>
        )}
      </p>
      {!reauth && (
        <p className="mt-2 text-[12.5px] leading-relaxed text-subtle">
          If Robinhood offers an account-type choice while you set up your agentic account,
          pick <b className="text-ink">limited margin</b> — your trades execute without
          settlement delays, with no borrowing and no leverage. Miss it? No problem — the
          next step offers a one-click switch.
        </p>
      )}

      {/* The what-happens-next story must come BEFORE the member leaves: the
          "can't connect page is expected" warning used to live only on the
          paste step, first seen AFTER the member was already stranded there.
          The animated three-step explainer (chips + browser scenes) sells the
          dead-end page as expected rather than broken; it freezes to a static
          list under prefers-reduced-motion. */}
      <div className="mt-4">
        <ConnectExplainer />
      </div>

      <Button variant="go" className="mt-4 w-full" onClick={connect} busy={busy}>
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
          strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
        {reauth ? "Reconnect Robinhood" : "Connect Robinhood"}
      </Button>
      {err && <div className="mt-3 text-[13px] leading-relaxed text-red">⚠ {err}</div>}
    </div>
  );
}
