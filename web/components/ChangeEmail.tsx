"use client";

// The quiet email row under the display name on Account: the current address
// with a "Change" affordance that expands into the inline two-step flow —
// enter the new address (a 6-digit code goes THERE), then the code. The
// backend swaps members.email only on a correct code, so the account can never
// end up pointing at an inbox nobody proved. Deliberately not a Card, matching
// NameRow's style.

import { useState } from "react";
import { api } from "@/lib/api";
import type { Member } from "@/lib/types";

export function ChangeEmail({ member, onChanged }: { member: Member; onChanged: (m: Member) => void }) {
  const [phase, setPhase] = useState<"idle" | "email" | "code">("idle");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reset = () => { setPhase("idle"); setEmail(""); setCode(""); setErr(null); };

  async function start() {
    const e = email.trim().toLowerCase();
    if (!e || busy) return;
    setBusy(true); setErr(null);
    try { await api.emailChangeStart(e); setCode(""); setPhase("code"); }
    catch (er: any) { setErr(er?.message || "couldn't send the code"); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!code.trim() || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.emailChangeConfirm(email.trim().toLowerCase(), code.trim());
      onChanged(r.member);
      reset();
    } catch (er: any) { setErr(er?.message || "that code didn't work"); }
    finally { setBusy(false); }
  }

  const inputCls =
    "rounded-[8px] border border-teal/40 bg-white/[0.05] px-2 py-1 text-[13px] text-ink " +
    "outline-none focus:border-teal/70 disabled:opacity-50";

  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted">
      {phase === "idle" && (
        <>
          <span>{member.email}</span>
          <button onClick={() => { setEmail(""); setErr(null); setPhase("email"); }}
            className="text-[12px] text-subtle transition hover:text-teal">Change</button>
        </>
      )}
      {phase === "email" && (
        <>
          <input autoFocus type="email" value={email} disabled={busy} aria-label="New email"
            placeholder="new@email.com" spellCheck={false} autoComplete="email"
            onChange={(e) => { setEmail(e.target.value); setErr(null); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); start(); }
              else if (e.key === "Escape") reset();
            }}
            className={`w-[220px] ${inputCls}`} />
          <button onClick={start} disabled={busy || !email.trim()}
            className="text-[12px] font-semibold text-teal disabled:opacity-50">
            {busy ? "sending…" : "Send code"}
          </button>
          <button onClick={reset} className="text-[12px] text-subtle hover:text-ink">cancel</button>
        </>
      )}
      {phase === "code" && (
        <>
          <span className="text-[12px] text-subtle">code sent to <b className="text-ink">{email.trim().toLowerCase()}</b> —</span>
          <input autoFocus value={code} disabled={busy} aria-label="Verification code"
            placeholder="6-digit code" inputMode="numeric" maxLength={6} spellCheck={false}
            onChange={(e) => { setCode(e.target.value.replace(/\D/g, "")); setErr(null); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); confirm(); }
              else if (e.key === "Escape") reset();
            }}
            className={`w-[110px] tnum ${inputCls}`} />
          <button onClick={confirm} disabled={busy || code.trim().length < 6}
            className="text-[12px] font-semibold text-teal disabled:opacity-50">
            {busy ? "confirming…" : "Confirm"}
          </button>
          <button onClick={reset} className="text-[12px] text-subtle hover:text-ink">cancel</button>
        </>
      )}
      {err && <span className="text-[12px] text-red">⚠ {err}</span>}
    </div>
  );
}
