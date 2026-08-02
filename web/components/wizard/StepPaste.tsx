"use client";

// Step 3 — paste the URL back. Robinhood's agentic OAuth only redirects to
// localhost, so after approving, the member dead-ends on a browser "can't
// connect" page whose URL carries the ?code=&state= we need — this step is
// that ONE paste. On success we refresh the member, show a small
// "Connected ✓" beat, and auto-advance. "Re-open Robinhood" restarts the
// approval (fresh authorize URL) without leaving the step.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui";

export function StepPaste({ onDone }: { onDone: () => void }) {
  const { member, refreshMember } = useAuth();
  const rh = member?.robinhood;
  const connected = !!(rh?.status === "connected" && rh?.account_number);

  const [pasteUrl, setPasteUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Robinhood approvals sometimes expire before the paste (state expiry /
  // reused code) — after a failure we say so explicitly; redoing works.
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [justConnected, setJustConnected] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  // restart the approval — same flow step 2 ran
  async function reopen() {
    setErr(null);
    const tab = window.open("", "_blank"); // open synchronously (no popup block)
    try {
      const { authorize_url } = await api.rhStart();
      try { sessionStorage.setItem("concordia_oauth_inflight", "1"); } catch {}
      if (tab) tab.location.href = authorize_url; else window.location.href = authorize_url;
    } catch (e: any) {
      if (tab) tab.close();
      setErr(e?.message || "couldn't re-open Robinhood");
    }
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
        Approving lands you on a <b className="text-ink">&ldquo;can&rsquo;t connect&rdquo;</b> page —
        that&apos;s expected. Paste that page&apos;s <b className="text-ink">full address-bar URL</b> here.
      </p>

      <input
        value={pasteUrl}
        onChange={(e) => { setPasteUrl(e.target.value); setErr(null); }}
        onKeyDown={(e) => { if (e.key === "Enter" && pasteUrl.trim() && !busy) complete(); }}
        placeholder="http://localhost:8917/callback?code=…&state=…"
        className="input mt-3 w-full font-mono !text-[12px]" spellCheck={false} autoComplete="off"
      />
      <div className="mt-3 flex items-center gap-2.5">
        <Button variant="ok" onClick={complete} busy={busy} disabled={!pasteUrl.trim()}>Finish connecting</Button>
        <button onClick={reopen}
          className="ml-auto text-[12px] text-subtle hover:text-teal">Re-open Robinhood</button>
      </div>

      {err && (
        <div className="mt-3 text-[13px] leading-relaxed text-red">
          ⚠ {err}
          {failedAttempts >= 1 && (
            <div className="mt-1.5 text-[12.5px] text-amber">
              This happens — approvals sometimes expire before the paste.
              &ldquo;Re-open Robinhood&rdquo; and approve again; the second try almost always works.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
