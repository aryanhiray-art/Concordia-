"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { api, setToken } from "@/lib/api";
import type { Member } from "@/lib/types";
import { Button, Card, Logo, Wordmark } from "./ui";
import { GoogleButton, googleEnabled } from "./GoogleButton";
import { PrivacyDoc, TermsDoc } from "./legal";

// The required club-agreement checkbox — shared by password signup and the Google finish step.
function ConsentRow({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer select-none items-start gap-2.5">
      <input
        type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        className="mt-[3px] h-4 w-4 flex-none cursor-pointer accent-teal"
      />
      <span className="text-[12.5px] leading-relaxed text-muted">
        I&apos;ve read and agree to the{" "}
        <a href="/terms" target="_blank" rel="noopener noreferrer"
          className="font-semibold text-teal hover:underline">Club Participation Agreement</a>,
        including its risk disclosure, and the{" "}
        <a href="/privacy" target="_blank" rel="noopener noreferrer"
          className="font-semibold text-teal hover:underline">Privacy Policy</a>{" "}
        (both may be updated with notice).
      </span>
    </label>
  );
}

function OrDivider() {
  return (
    <div className="my-5 flex items-center gap-3" aria-hidden>
      <span className="h-px flex-1 bg-white/10" />
      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle">or</span>
      <span className="h-px flex-1 bg-white/10" />
    </div>
  );
}

export function AuthGate({ onAuthed }: { onAuthed: (m: Member) => void }) {
  const pathname = usePathname();
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [invite, setInvite] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Google state machine: a credential parked here means Google verified the user but the
  // backend wants invite + consent (first-time member) — we show the finish step and retry.
  const [gCred, setGCred] = useState<string | null>(null);

  // AppShell shows AuthGate on every route when logged out, so the legal pages must
  // render here too — signup users follow the agreement links before they have an account.
  if (pathname === "/terms" || pathname === "/privacy") {
    return (
      <div className="mx-auto w-[min(820px,100%)] px-5 pb-24 pt-12">
        <a href="/" className="mb-7 flex items-center gap-3">
          <Logo size={34} />
          <Wordmark />
        </a>
        {pathname === "/terms" ? <TermsDoc /> : <PrivacyDoc />}
      </div>
    );
  }

  async function finishGoogle(credential: string, inviteCode?: string, agreed?: boolean) {
    setErr(null); setBusy(true);
    try {
      const res = await api.googleLogin(credential, inviteCode || undefined, agreed ? true : undefined);
      setToken(res.token);
      onAuthed(res.member);
    } catch (e: any) {
      const msg = String(e?.message || "Google sign-in failed");
      const needsSignupStep = /invite|agreement/i.test(msg);
      if (needsSignupStep && !gCred) {
        setGCred(credential); // new member → reveal the invite + consent step, no error yet
      } else {
        setErr(msg);
        if (needsSignupStep) setGCred(credential);
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (mode === "signup" && !consent) { setErr("please accept the club agreement first"); return; }
    setErr(null); setBusy(true);
    try {
      const res = mode === "signup"
        ? await api.signup(invite.trim(), email.trim(), password, name.trim(), consent)
        : await api.login(email.trim(), password);
      setToken(res.token);
      onAuthed(res.member);
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-[12vh] w-[min(440px,100%)] px-5">
      <div className="mb-7 flex flex-col items-center text-center">
        <Logo size={56} />
        <div className="mt-4 font-display text-[26px] font-bold tracking-tight text-[#8FD9C0]">
          concordia
        </div>
        <p className="mt-1.5 text-[13.5px] text-muted">
          A trading club that votes. <b className="font-semibold text-teal">One ballot a day</b>,
          executed on your own Robinhood.
        </p>
      </div>

      <Card>
        {gCred ? (
          /* ── Google finish step: verified with Google, new member → invite + consent ── */
          <div className="space-y-3.5">
            <div>
              <div className="font-display text-[16px] font-bold">Almost in</div>
              <p className="mt-1 text-[13px] leading-relaxed text-muted">
                Google verified you, and you&apos;re new here — new members need a club invite
                and to accept the club agreement.
              </p>
            </div>
            <div>
              <label className="label mb-2">Club invite code</label>
              <input className="input" placeholder="forge-concord-…" value={invite}
                onChange={(e) => setInvite(e.target.value)} spellCheck={false} autoComplete="off" />
            </div>
            <ConsentRow checked={consent} onChange={setConsent} />
            {err && <div className="text-[13px] text-red">⚠ {err}</div>}
            <Button variant="go" className="w-full !py-3.5" busy={busy}
              disabled={!invite.trim() || !consent}
              onClick={() => finishGoogle(gCred, invite.trim(), consent)}>
              Finish sign-up
            </Button>
            <button type="button"
              className="w-full text-center text-[12px] text-subtle transition-colors hover:text-ink"
              onClick={() => { setGCred(null); setErr(null); }}>
              ← Sign in a different way
            </button>
          </div>
        ) : (
          <>
            <div className="mb-5 flex gap-3 text-[13px]">
              <button onClick={() => { setMode("signup"); setErr(null); }}
                className={mode === "signup" ? "font-semibold text-teal" : "text-subtle"}>Create account</button>
              <span className="text-subtle/50">·</span>
              <button onClick={() => { setMode("login"); setErr(null); }}
                className={mode === "login" ? "font-semibold text-teal" : "text-subtle"}>Log in</button>
            </div>

            {googleEnabled && (
              <>
                <GoogleButton onCredential={(c) =>
                  finishGoogle(c,
                    mode === "signup" ? invite.trim() : undefined,
                    mode === "signup" && consent ? true : undefined)} />
                <OrDivider />
              </>
            )}

            <form className="space-y-3.5" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              {mode === "signup" && (
                <div>
                  <label className="label mb-2">Club invite code</label>
                  <input className="input" placeholder="forge-concord-…" value={invite}
                    onChange={(e) => setInvite(e.target.value)} spellCheck={false} autoComplete="off" />
                </div>
              )}
              <div>
                <label className="label mb-2">Email</label>
                <input className="input" placeholder="you@email.com" type="email" value={email}
                  onChange={(e) => setEmail(e.target.value)} spellCheck={false} autoComplete="email" />
              </div>
              {mode === "signup" && (
                <div>
                  <label className="label mb-2">Display name</label>
                  <input className="input" placeholder="Your name" value={name}
                    onChange={(e) => setName(e.target.value)} autoComplete="name" />
                </div>
              )}
              <div>
                <label className="label mb-2">Password</label>
                <div className="relative">
                  <input className="input pr-11" placeholder={mode === "signup" ? "Create a password (8+ chars)" : "Your password"}
                    type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                    autoComplete={mode === "signup" ? "new-password" : "current-password"} />
                  <button type="button" onClick={() => setShowPw((s) => !s)} tabIndex={-1}
                    aria-label={showPw ? "Hide password" : "Show password"} title={showPw ? "Hide password" : "Show password"}
                    className="absolute inset-y-0 right-0 flex items-center pr-3.5 text-subtle transition-colors hover:text-teal">
                    {showPw ? (
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c7 0 10 8 10 8a18.4 18.4 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><path d="M6.6 6.6A18.4 18.4 0 0 0 2 12s3 8 10 8a9.1 9.1 0 0 0 5.4-1.6" /><path d="M2 2l20 20" /></svg>
                    ) : (
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8Z" /><circle cx="12" cy="12" r="3" /></svg>
                    )}
                  </button>
                </div>
              </div>
              {mode === "signup" && <ConsentRow checked={consent} onChange={setConsent} />}
              {err && <div className="text-[13px] text-red">⚠ {err}</div>}
              <Button type="submit" variant="go" className="w-full !py-3.5" busy={busy}
                disabled={mode === "signup" && !consent}>
                {mode === "signup" ? "Create account" : "Log in"}
              </Button>
            </form>
          </>
        )}
      </Card>

      {mode === "login" && !gCred && (
        <p className="mt-3 text-center text-[12px] text-subtle">
          Forgot your password? Ask the club owner to reset it — they&apos;ll give you a
          temporary one you can change after logging in.
        </p>
      )}

      <p className="mt-4 text-center text-[11.5px] leading-relaxed text-subtle">
        Invite-only. You connect Robinhood through Robinhood&apos;s own secure login —
        we never see your password.
        {" "}
        <a href="/terms" target="_blank" rel="noopener noreferrer" className="text-muted hover:text-teal hover:underline">Terms</a>
        {" · "}
        <a href="/privacy" target="_blank" rel="noopener noreferrer" className="text-muted hover:text-teal hover:underline">Privacy</a>
      </p>
    </div>
  );
}
