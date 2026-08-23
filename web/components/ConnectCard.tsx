"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Member } from "@/lib/types";
import { ConnectExplainer } from "./ConnectExplainer";
import { Button, Card, Pill, Toggle } from "./ui";

type MarginStatus = Awaited<ReturnType<typeof api.marginStatus>>;

/** Account-type line for the connected state: limited margin trades on
 *  unsettled funds (no borrowing/leverage); a cash account waits T+1 after
 *  every sell, so we surface the one-click switch here too. */
function MarginBadge() {
  const [m, setM] = useState<MarginStatus | null>(null);
  useEffect(() => {
    let alive = true;
    api.marginStatus()
      .then((r) => { if (alive) setM(r); })
      .catch(() => { /* quiet — the badge is informational */ });
    return () => { alive = false; };
  }, []);
  // Margin members see NOTHING here (owner call: once switched, stop talking
  // about it) — the badge exists only to nudge cash accounts to the switch.
  if (!m?.connected || !m.account_type || m.account_type !== "cash") return null;
  return (
    <div className="mt-1.5 text-[12.5px] text-amber">
      Cash account — sale proceeds settle overnight before they redeploy.{" "}
      {m.upgrade && (
        <a
          className="underline hover:text-ink"
          href={m.upgrade.web_upgrade_url}
          target="_blank" rel="noopener noreferrer"
        >
          Switch to limited margin
        </a>
      )}
    </div>
  );
}

export function ConnectCard({ member, onChange }: { member: Member; onChange: () => void }) {
  const rh = member.robinhood;
  const s = member.settings;
  const connected = rh && rh.status === "connected" && rh.account_number;
  const agentic = rh && Number(rh.agentic_allowed) === 1;
  // completed the flow before but the tokens are gone — Robinhood rotated them
  // ("error") or the member disconnected ("revoked"). Same connect flow reattaches.
  const reauth = !!(rh && rh.account_number && !connected);

  const [optedIn, setOptedIn] = useState(Number(s?.opted_in) === 1);
  // default ON when the column hasn't arrived yet (older backend payloads)
  const [reminders, setReminders] = useState(Number(s?.email_reminders ?? 1) === 1);
  const [busy, setBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [phase, setPhase] = useState<"idle" | "paste">("idle");
  const [pasteUrl, setPasteUrl] = useState("");
  const [err, setErr] = useState<string | null>(null);
  // how many times the paste-back exchange has failed this session — Robinhood
  // approvals sometimes expire before the paste (state expiry / reused code), and
  // simply redoing the flow works; after a failure we say so explicitly.
  const [failedAttempts, setFailedAttempts] = useState(0);

  // Robinhood's agentic OAuth only redirects to localhost, so we can't catch the
  // callback server-side. Open Robinhood in a new tab; after approving, the user
  // lands on a "can't connect" localhost page — they copy that URL back here.
  async function connect() {
    setBusy(true); setErr(null);
    const tab = window.open("", "_blank");           // open synchronously (no popup block)
    try {
      const { authorize_url } = await api.rhStart();
      if (tab) tab.location.href = authorize_url; else window.location.href = authorize_url;
      setPhase("paste");
    } catch (e: any) {
      if (tab) tab.close();
      setErr(e.message || "couldn't start Robinhood connect");
    } finally { setBusy(false); }
  }

  async function complete() {
    setBusy(true); setErr(null);
    // members paste with stray whitespace / wrapping quotes — clean before submit
    const url = pasteUrl.trim().replace(/^["'<]+|["'>]+$/g, "");
    try { await api.rhComplete(url); setPhase("idle"); setPasteUrl(""); setFailedAttempts(0); onChange(); }
    catch (e: any) { setFailedAttempts((n) => n + 1); setErr(e.message || "couldn't finish connecting"); }
    finally { setBusy(false); }
  }

  // The single on/off switch — persists immediately, stays where you leave it.
  async function toggle(on: boolean) {
    setOptedIn(on);
    try { await api.participate(on); onChange(); }
    catch { setOptedIn(!on); } // revert on failure
  }

  async function toggleReminders(on: boolean) {
    setReminders(on);
    try { await api.emailReminders(on); onChange(); }
    catch { setReminders(!on); } // revert on failure
  }

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="label">Your account</h2>
        {connected
          ? (agentic ? <Pill tone="ok">● connected · agentic</Pill> : <Pill tone="warn">connected · not agentic</Pill>)
          : reauth ? <Pill tone="warn">reconnect needed</Pill>
          : <Pill tone="danger">not connected</Pill>}
      </div>

      {!connected ? (
        <div className="mt-4">
          <p className="text-[13.5px] leading-relaxed text-muted">
            {reauth ? (
              rh!.status === "error"
                ? "Robinhood rotates credentials periodically — reconnect to resume trading. Under a minute."
                : "This account was disconnected — reconnect to resume trading."
            ) : (
              <>Connect Robinhood to take part. You approve on Robinhood&apos;s own site —
              your credentials never touch Concordia.</>
            )}
          </p>

          {phase === "idle" ? (
            <>
              <ConnectExplainer />
              <Button variant="go" className="mt-4" onClick={connect} busy={busy}>
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
                <li>It’ll land on a <b className="text-ink">“can’t connect / localhost”</b> page — that’s expected.</li>
                <li>Copy that page’s <b className="text-ink">full address bar URL</b> and paste it below.</li>
              </ol>
              <input
                value={pasteUrl} onChange={(e) => { setPasteUrl(e.target.value); setErr(null); }}
                onKeyDown={(e) => { if (e.key === "Enter" && pasteUrl.trim() && !busy) complete(); }}
                placeholder="http://localhost:8917/callback?code=…&state=…"
                className="input mt-3 w-full font-mono !text-[12px]" spellCheck={false} autoComplete="off" />
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
                  This happens — approvals sometimes expire before the paste.
                  &ldquo;Re-open Robinhood&rdquo; and approve again; the second try almost always works.
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="mt-3 text-[13px] text-muted">
            Account <span className="font-display text-ink">···{String(rh!.account_number).slice(-4)}</span>
            {!agentic && <span className="ml-2 text-amber">— enable Robinhood&apos;s “Agentic” account to trade.</span>}
          </div>
          <MarginBadge />

          <div className="mt-5 flex items-center justify-between gap-4 border-t border-white/10 pt-5">
            <div>
              <div className="font-display text-[15px] font-semibold">
                {optedIn ? <span className="text-teal">Participating today</span> : "Sitting out today"}
              </div>
            </div>
            <Toggle on={optedIn} onChange={toggle} />
          </div>

          <div className="mt-4 flex items-center justify-between gap-4 border-t border-white/10 pt-4">
            <div>
              <div className="font-display text-[13.5px] font-semibold">Ballot reminder emails</div>
              <div className="mt-0.5 text-[12px] text-subtle">When the ballot opens, and a nudge before it closes if you haven&apos;t voted.</div>
            </div>
            <Toggle on={reminders} onChange={toggleReminders} />
          </div>

          {/* the exit right §7 of the club agreement promises: full revocation */}
          <div className="mt-4 border-t border-white/10 pt-3 text-[12px] text-subtle">
            {!confirmDisconnect ? (
              <Button variant="danger" onClick={() => setConfirmDisconnect(true)}>
                Disconnect Robinhood
              </Button>
            ) : (
              <div className="space-y-2.5">
                <p className="leading-relaxed">
                  Revokes the club&apos;s access to your account entirely — trading for you stops and
                  reconnecting later requires the full approval flow. Your positions are untouched.
                </p>
                <div className="flex flex-wrap items-center gap-2.5">
                  <Button variant="danger" className="h-10" busy={busy}
                    onClick={async () => {
                      setBusy(true);
                      try { await api.rhDisconnect(); setConfirmDisconnect(false); onChange(); }
                      catch (e: any) { setErr(e.message || "couldn't disconnect"); }
                      finally { setBusy(false); }
                    }}>
                    Yes, disconnect
                  </Button>
                  <button onClick={() => setConfirmDisconnect(false)} className="hover:text-ink">cancel</button>
                </div>
              </div>
            )}
            {err && <div className="mt-2 text-red">⚠ {err}</div>}
          </div>
        </>
      )}
    </Card>
  );
}
