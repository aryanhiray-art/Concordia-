"use client";

import { useState } from "react";
import { api, setToken } from "@/lib/api";
import type { Member } from "@/lib/types";
import { Button, Card, Logo } from "./ui";

export function AuthGate({ onAuthed }: { onAuthed: (m: Member) => void }) {
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [invite, setInvite] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr(null); setBusy(true);
    try {
      const res = mode === "signup"
        ? await api.signup(invite.trim(), email.trim(), password, name.trim())
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
        <Logo size={52} />
        <div className="mt-4 font-display text-[26px] font-bold tracking-tight">
          con<span className="text-teal">·</span>cord
        </div>
        <p className="mt-1.5 text-[13.5px] text-muted">
          A trading club that votes. <b className="font-semibold text-teal">One ballot a day</b>,
          executed on your own Robinhood.
        </p>
      </div>

      <Card>
        <div className="mb-5 flex gap-3 text-[13px]">
          <button onClick={() => setMode("signup")}
            className={mode === "signup" ? "font-semibold text-teal" : "text-subtle"}>Create account</button>
          <span className="text-subtle/50">·</span>
          <button onClick={() => setMode("login")}
            className={mode === "login" ? "font-semibold text-teal" : "text-subtle"}>Log in</button>
        </div>

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
            <input className="input" placeholder={mode === "signup" ? "Create a password (8+ chars)" : "Your password"}
              type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signup" ? "new-password" : "current-password"} />
          </div>
          {err && <div className="text-[13px] text-red">⚠ {err}</div>}
          <Button type="submit" variant="go" className="w-full !py-3.5" busy={busy}>
            {mode === "signup" ? "Create account" : "Log in"}
          </Button>
        </form>
      </Card>

      <p className="mt-4 text-center text-[11.5px] leading-relaxed text-subtle">
        Invite-only. You connect Robinhood through Robinhood&apos;s own secure login —
        we never see your password.
      </p>
    </div>
  );
}
