"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { api, setToken } from "@/lib/api";
import { isAdminHost, isDemoHost } from "@/lib/host";
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
          className="font-medium text-teal hover:underline">Club Participation Agreement</a>,
        including its risk disclosure, and the{" "}
        <a href="/privacy" target="_blank" rel="noopener noreferrer"
          className="font-medium text-teal hover:underline">Privacy Policy</a>{" "}
        (both may be updated with notice).
      </span>
    </label>
  );
}

// 6-digit emailed code — one input, digits only, styled for OTP entry.
// Shared by the signup verification step, the password-reset step, and the
// legacy-member ConfirmEmail interstitial (hence the export).
export function CodeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <input
      className="input text-center text-[17px] font-semibold tracking-[0.45em]"
      inputMode="numeric" maxLength={6} autoComplete="one-time-code"
      placeholder="••••••" spellCheck={false} value={value}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
    />
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

// One step of the desktop brand pane's "How it works" story. One visual anchor
// only — a large light gradient numeral (the earlier chip+number pairing gave
// every row two competing leads). body is a string on purpose: no inline
// bolds/accents ever creep back in.
function HowRow({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <div className="flex items-start gap-5">
      <span className="hiw-num" aria-hidden>{`0${n}`}</span>
      <div className="min-w-0 pt-[5px]">
        <h3 className="font-display text-[15px] font-semibold leading-tight text-ink">{title}</h3>
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{body}</p>
      </div>
    </div>
  );
}

// Desktop-only (lg+) left pane: the mark, the wordmark, and the three-step
// story. Below lg the stacked mobile header carries the tagline instead.
function BrandPane() {
  const demo = isDemoHost();
  return (
    <div className="relative hidden min-w-0 lg:flex lg:flex-col lg:justify-center">
      <div className="authglow" aria-hidden />
      <div className="relative">
        <div className="flex items-center gap-[18px]">
          <span className="lg-draw flex-none" aria-hidden>
            <Logo size={76} />
          </span>
          <div className="font-display text-[52px] font-bold leading-none tracking-tight text-[#8FD9C0]">
            concordia{demo && <span className="text-[0.62em] font-semibold text-teal/70">.demo</span>}
          </div>
        </div>
        {/* The slogan claims the FIGHT, never the result — "beating" would be
            an implied performance promise. Wordmark + slogan are deliberately
            the two largest text elements on the page; the mechanics live in
            the How-it-works panel below. */}
        {/* One line at xl (measured: 477px in a 520px column), balanced two-line
            wrap at lg (440px column) — nowrap alone overflowed at 1024. */}
        <p className="mt-7 text-[21px] font-light leading-snug text-ink [text-wrap:balance] xl:text-[22px]">
          Taking on Wall Street, <span className="text-teal">one vote at a time</span>.
        </p>
        {/* The owner's favorite glass finish wraps the story itself — a real
            material anchor for the pane with no fake product data (live vote
            counts are members-only; ballots go public only after they close). */}
        <div className="enter mt-9" style={{ animationDelay: "0.15s" }}>
          <div className="glass glass-glow shine p-6 xl:p-7">
            <p className="label">{demo ? "How the demo works" : "How it works"}</p>
            <div className="stagger mt-5 space-y-6">
              {demo ? (
                <>
                  <HowRow n={1} title="Use the invite code: demo"
                    body="The playground is open to everyone." />
                  <HowRow n={2} title="Use your real email"
                    body="We email you a 6-digit code to confirm it, just like the club." />
                  <HowRow n={3} title="Pick any name and password"
                    body="Demo accounts are fully separate from the real club." />
                </>
              ) : (
                <>
                  <HowRow n={1} title="Vote on the basket"
                    body="Every member votes for their stocks — the top picks become the club's basket." />
                  <HowRow n={2} title="You set the stake"
                    body="You choose how much of your account plays, and only that slice ever trades." />
                  <HowRow n={3} title="Your account stays yours"
                    body="Every trade runs in your own brokerage account — the club never touches your money." />
                </>
              )}
            </div>
            {demo && (
              <p className="mt-5 border-t border-white/10 pt-4 text-[12.5px] leading-relaxed text-subtle">
                Everything works like the real club — except Robinhood is simulated
                and no real money ever moves.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// `notice` = why the last session ended (today: the 2-hour idle logout). It
// opens the card on the LOG IN tab and prints a quiet line above the form —
// an expired session is a normal event, never an error.
export function AuthGate({ onAuthed, notice }: { onAuthed: (m: Member) => void; notice?: string | null }) {
  const pathname = usePathname();
  // On admin.voteconcordia.com the same login card serves the ops console —
  // but the GIS origin isn't authorized for the admin subdomain, so Google
  // sign-in is hidden there (email/password only). AuthGate never renders
  // server-side (AppShell gates it behind `ready`), so this is stable.
  const adminHost = isAdminHost();
  // demo.voteconcordia.com: fake-money playground — ".demo" branding, no
  // Google (origin unauthorized). Codes travel by REAL email, same as the club.
  const demoHost = isDemoHost();
  const [mode, setMode] = useState<"signup" | "login">(notice ? "login" : "signup");
  // "You were signed out after 2 hours idle" explains ONE arrival at this
  // screen. Leaving the login tab means it has been read; without this it
  // re-appears every time you toggle back, long after it stopped being true.
  const [dismissedNotice, setDismissedNotice] = useState(false);
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
  // Signup email-verification: true = the card shows the 6-digit-code step
  // (form entries stay in state so "← back" loses nothing).
  const [codeStep, setCodeStep] = useState(false);
  const [emailCode, setEmailCode] = useState("");
  const [resent, setResent] = useState(false);
  // Forgot-password flow (login mode): null = not in it; "email" → "code" → done,
  // "owner" = backend can't send mail, show the ask-the-owner fallback.
  const [forgotStep, setForgotStep] = useState<null | "email" | "code" | "owner">(null);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotCode, setForgotCode] = useState("");
  const [forgotNewPw, setForgotNewPw] = useState("");
  // Green success note shown on the login form after a completed reset.
  const [note, setNote] = useState<string | null>(null);

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
    // Check the password BEFORE signupStart: that call emails a real code and
    // burns a slot in two rate-limit windows, so a password the server will
    // reject at the final step must not cost the member a send first.
    if (mode === "signup" && password.length < 8) { setErr("password must be at least 8 characters"); return; }
    setErr(null); setNote(null); setBusy(true);
    try {
      if (mode === "signup") {
        // Pre-flight: validates the invite and, if verification is on, emails a code.
        const start = await api.signupStart(invite.trim(), email.trim());
        if (start.code_required) { setEmailCode(""); setCodeStep(true); return; }
        const res = await api.signup(invite.trim(), email.trim(), password, name.trim(), consent);
        setToken(res.token);
        onAuthed(res.member);
      } else {
        const res = await api.login(email.trim(), password);
        setToken(res.token);
        onAuthed(res.member);
      }
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  // Code step "Verify & create account" — same signup call, plus the emailed code.
  async function verifySignup() {
    setErr(null); setBusy(true);
    try {
      const res = await api.signup(invite.trim(), email.trim(), password, name.trim(), consent, emailCode.trim());
      setToken(res.token);
      onAuthed(res.member);
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  // Explicit resend only — never re-fires on keystrokes.
  async function resendCode() {
    setErr(null); setBusy(true);
    try {
      await api.signupStart(invite.trim(), email.trim());
      setResent(true);
      setTimeout(() => setResent(false), 2500);
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendResetCode() {
    setErr(null); setBusy(true);
    try {
      const res = await api.forgot(forgotEmail.trim());
      if (res.email_enabled) { setForgotCode(""); setForgotNewPw(""); setForgotStep("code"); }
      else setForgotStep("owner"); // club can't send mail → ask-the-owner fallback
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  async function doReset() {
    setErr(null); setBusy(true);
    try {
      await api.resetPassword(forgotEmail.trim(), forgotCode.trim(), forgotNewPw);
      // Back to the login form, email prefilled, with a green all-done note.
      setEmail(forgotEmail.trim());
      setPassword("");
      setForgotStep(null);
      setNote("Password reset — log in with your new password.");
    } catch (e: any) {
      setErr(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  function backToLogin() {
    setForgotStep(null); setErr(null);
  }

  return (
    // Below lg: exactly the original stacked column (logo → wordmark → tagline → card).
    // lg+: a centered two-pane composition — brand pane · fading hairline · auth pane.
    <div className="lg:flex lg:min-h-screen lg:px-12 lg:py-12 lg:[align-items:safe_center] lg:[justify-content:safe_center]">
      {/* Fixed left track (not 1fr): a stretching column left the divider
          hugging the auth card — measured 215px of void on one side vs 65px on
          the other. Fixed tracks + justify-center put the hairline dead centre
          in an even gutter, so the two panels read as a designed pair. */}
      <div className="lg:grid lg:w-full lg:max-w-[1200px] lg:grid-cols-[440px_1px_400px] lg:items-center lg:justify-center lg:gap-x-12 xl:grid-cols-[520px_1px_440px] xl:gap-x-16">
        <BrandPane />
        {/* now that it sits on the true centre axis it earns being visible —
            /15 over near-black did not register on screen at all */}
        <div className="hidden h-[min(560px,64vh)] w-px self-center bg-gradient-to-b from-transparent via-white/25 to-transparent lg:block" aria-hidden />

        <div className="mx-auto mt-[12vh] w-[min(440px,100%)] px-5 lg:mx-0 lg:mt-0 lg:w-full lg:px-0">
      <div className="mb-7 flex flex-col items-center text-center lg:hidden">
        <span className="lg-draw" aria-hidden>
          <Logo size={56} />
        </span>
        <div className="mt-4 font-display text-[26px] font-bold tracking-tight text-[#8FD9C0]">
          concordia{demoHost && <span className="text-[0.62em] font-semibold text-teal/70">.demo</span>}
        </div>
        <p className="mt-2.5 text-[17px] font-light leading-snug text-ink">
          Taking on Wall Street, <span className="text-teal">one vote at a time</span>.
        </p>
        {/* The desktop BrandPane carries "How the demo works" (incl. the invite
            code); it is lg-only, so a PHONE visitor had nothing but the input
            placeholder to guess the code from. One line covers the essentials. */}
        {demoHost && (
          <p className="mt-3 text-[13px] leading-relaxed text-muted">
            Invite code <b className="text-teal">demo</b> · use your real email —
            we send a 6-digit code.
          </p>
        )}
      </div>

      {/* obvious "you're on the ops console, not the member site" marker */}
      {adminHost && <p className="label mb-3 text-center">Admin console</p>}

      <Card className="glass-glow">
        {gCred ? (
          /* ── Google finish step: verified with Google, new member → invite + consent ── */
          <div className="space-y-3.5">
            <div>
              <div className="font-display text-[16px] font-bold">Almost in</div>
              <p className="mt-1 text-[13px] leading-relaxed text-muted">
                Enter your club invite and accept the agreement.
              </p>
            </div>
            <div>
              <label className="label mb-2">Invite code</label>
              <input className="input" placeholder={isDemoHost() ? "demo" : "Code"} value={invite}
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
        ) : codeStep ? (
          /* ── Signup email-verification step: form state is kept, "← back" loses nothing ── */
          <div className="space-y-3.5">
            <div>
              <div className="font-display text-[16px] font-bold">Check your email</div>
              <p className="mt-1 text-[13px] leading-relaxed text-muted">
                We emailed a 6-digit code to{" "}
                <span className="text-teal">{email.trim()}</span>.
              </p>
            </div>
            <div>
              <label className="label mb-2">Verification code</label>
              <CodeInput value={emailCode} onChange={setEmailCode} />
            </div>
            {err && <div className="text-[13px] text-red">⚠ {err}</div>}
            <Button variant="go" className="w-full !py-3.5" busy={busy}
              disabled={emailCode.trim().length !== 6}
              onClick={verifySignup}>
              Verify &amp; create account
            </Button>
            <div className="flex items-center justify-between text-[12px]">
              <button type="button" disabled={busy}
                className="text-subtle transition-colors hover:text-ink disabled:opacity-50"
                onClick={() => { setCodeStep(false); setErr(null); }}>
                ← back
              </button>
              <button type="button" disabled={busy || resent} onClick={resendCode}
                className="text-subtle transition-colors hover:text-teal disabled:opacity-50">
                {resent ? "sent ✓" : "Resend code"}
              </button>
            </div>
          </div>
        ) : mode === "login" && forgotStep ? (
          /* ── Self-serve password reset (login mode) ── */
          <div className="space-y-3.5">
            {forgotStep === "email" && (
              <>
                <div className="font-display text-[16px] font-bold">Reset your password</div>
                <div>
                  <label className="label mb-2">Email</label>
                  <input className="input" placeholder="you@email.com" type="email" value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)} spellCheck={false} autoComplete="email" />
                </div>
                {err && <div className="text-[13px] text-red">⚠ {err}</div>}
                <Button variant="go" className="w-full !py-3.5" busy={busy}
                  disabled={!forgotEmail.trim()} onClick={sendResetCode}>
                  Email me a code
                </Button>
              </>
            )}
            {forgotStep === "owner" && (
              <>
                <div className="font-display text-[16px] font-bold">Reset your password</div>
                <p className="text-[13px] leading-relaxed text-muted">
                  This club can&apos;t send email right now — ask the club owner for a
                  temporary password.
                </p>
              </>
            )}
            {forgotStep === "code" && (
              <>
                <div>
                  <div className="font-display text-[16px] font-bold">Check your email</div>
                  <p className="mt-1 text-[13px] leading-relaxed text-muted">
                    If <span className="text-teal">{forgotEmail.trim()}</span> has an
                    account, we emailed it a 6-digit code.
                  </p>
                </div>
                <div>
                  <label className="label mb-2">Reset code</label>
                  <CodeInput value={forgotCode} onChange={setForgotCode} />
                </div>
                <div>
                  <label className="label mb-2">New password</label>
                  <input className="input" placeholder="New password (8+ chars)" type="password"
                    value={forgotNewPw} onChange={(e) => setForgotNewPw(e.target.value)}
                    autoComplete="new-password" />
                </div>
                {err && <div className="text-[13px] text-red">⚠ {err}</div>}
                <Button variant="go" className="w-full !py-3.5" busy={busy}
                  disabled={forgotCode.trim().length !== 6 || forgotNewPw.length < 8}
                  onClick={doReset}>
                  Reset password
                </Button>
              </>
            )}
            <button type="button" disabled={busy}
              className="w-full text-center text-[12px] text-subtle transition-colors hover:text-ink disabled:opacity-50"
              onClick={backToLogin}>
              ← back to log in
            </button>
          </div>
        ) : (
          <>
            <div className="mb-5 flex gap-3 text-[13px]">
              <button onClick={() => { setMode("signup"); setErr(null); setNote(null); }}
                className={mode === "signup" ? "font-semibold text-teal" : "text-subtle"}>Create account</button>
              <span className="text-subtle/50">·</span>
              <button onClick={() => { setMode("login"); setErr(null); setDismissedNotice(true); }}
                className={mode === "login" ? "font-semibold text-teal" : "text-subtle"}>Log in</button>
            </div>

            {notice && mode === "login" && !dismissedNotice && (
              <p className="mb-4 rounded-[12px] border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-[12.5px] leading-relaxed text-muted">
                {notice}
              </p>
            )}

            {/* Demo included (owner call): demo.voteconcordia.com must be an
                Authorized JavaScript origin on the Google OAuth client or GIS
                refuses to render — the button only appears once the demo build
                carries NEXT_PUBLIC_GOOGLE_CLIENT_ID, and works once the origin
                is whitelisted in the owner's Google Cloud Console. */}
            {googleEnabled && !adminHost && (
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
                  <label className="label mb-2">Invite code</label>
                  <input className="input" placeholder={isDemoHost() ? "demo" : "Code"} value={invite}
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
              {note && mode === "login" && <div className="text-[13px] text-teal">✓ {note}</div>}
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
        <>
          {forgotStep === null && (
            <p className="mt-3 text-center text-[12px]">
              <button type="button"
                className="text-subtle transition-colors hover:text-teal hover:underline"
                onClick={() => {
                  setForgotStep("email");
                  setForgotEmail(email.trim()); // prefill from the login email field if set
                  setErr(null); setNote(null);
                }}>
                Forgot your password?
              </button>
            </p>
          )}
          {/* signup reaches these via the consent checkbox; login needs its own path */}
          <p className="mt-4 text-center text-[11.5px] text-subtle">
            <a href="/terms" target="_blank" rel="noopener noreferrer" className="text-muted hover:text-teal hover:underline">Terms</a>
            {" · "}
            <a href="/privacy" target="_blank" rel="noopener noreferrer" className="text-muted hover:text-teal hover:underline">Privacy</a>
          </p>
        </>
      )}

      {/* Signup-side caption only (the Google finish step also asks for an
          invite, so it stays there too) — a returning member logging in
          doesn't need to be told the door policy. */}
      {(mode === "signup" || gCred) && (
        <p className="mt-4 text-center text-[11.5px] text-subtle">
          Invite-only
        </p>
      )}
        </div>
      </div>
    </div>
  );
}
