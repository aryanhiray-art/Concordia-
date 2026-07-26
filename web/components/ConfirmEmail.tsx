"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CodeInput } from "./AuthGate";
import { Button, Card, Logo } from "./ui";

// Blocking interstitial for legacy members: the backend now verifies every
// member's email, and member.needs_email_verification gates the whole app
// (AppShell renders this INSTEAD of the app — verification precedes even the
// onboarding wizard). On mount we ask the backend to email a 6-digit code;
// confirming it flips the flag server-side, so a refreshMember() lets the
// app render.
export function ConfirmEmail() {
  const { member, refreshMember, logout } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  // Send exactly one code on mount — the ref survives React 18 strict mode's
  // dev-only double effect run, so the member never gets two emails at once.
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    api.verifyEmailStart()
      .then((res) => {
        // Raced a verification elsewhere (another tab, admin) — no code coming;
        // refetch the member so the flag clears and the app renders.
        if (!res.sent && res.already_verified) refreshMember();
      })
      .catch((e: any) => {
        // Reloading this gate a few times trips the send window — the code from
        // the earlier email still works, so a calm note beats a red error here.
        // Explicit "Resend code" clicks still surface the real 429.
        if (/too many codes/i.test(String(e?.message))) {
          setNote("We emailed you a code in the last few minutes — enter the newest one below.");
        } else {
          setErr(e?.message || "couldn't send the code");
        }
      });
  }, [refreshMember]);

  async function confirm() {
    setErr(null); setBusy(true);
    try {
      await api.verifyEmail(code.trim());
      await refreshMember(); // flag flipped server-side → AppShell renders the app
    } catch (e: any) {
      setErr(e?.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  // Explicit resend only — never re-fires on keystrokes.
  async function resend() {
    setErr(null); setBusy(true);
    try {
      const res = await api.verifyEmailStart();
      if (!res.sent && res.already_verified) { await refreshMember(); return; }
      setResent(true);
      setTimeout(() => setResent(false), 2500);
    } catch (e: any) {
      setErr(e?.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-5 py-10">
      <div className="w-[min(420px,100%)]">
        <Card className="enter">
          <div className="space-y-3.5">
            <Logo size={34} />
            <div>
              <div className="font-display text-[16px] font-bold">Confirm your email</div>
              <p className="mt-1 text-[13px] leading-relaxed text-muted">
                The club now verifies every member&apos;s email — we sent a 6-digit code
                to <span className="text-teal">{member?.email}</span>.
              </p>
            </div>
            <div>
              <label className="label mb-2">Verification code</label>
              <CodeInput value={code} onChange={setCode} />
            </div>
            {note && !err && <div className="text-[12.5px] text-muted">{note}</div>}
            {err && <div className="text-[13px] text-red">⚠ {err}</div>}
            <Button variant="go" className="w-full !py-3.5" busy={busy}
              disabled={code.trim().length !== 6} onClick={confirm}>
              Confirm
            </Button>
            <div className="flex items-center justify-between text-[12px]">
              <button type="button" disabled={busy} onClick={logout}
                className="text-subtle transition-colors hover:text-ink disabled:opacity-50">
                log out
              </button>
              <button type="button" disabled={busy || resent} onClick={resend}
                className="text-subtle transition-colors hover:text-teal disabled:opacity-50">
                {resent ? "sent ✓" : "Resend code"}
              </button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
