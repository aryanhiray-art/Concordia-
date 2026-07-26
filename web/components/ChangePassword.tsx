"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Card } from "./ui";

export function ChangePassword() {
  const { member, refreshMember } = useAuth();
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Only an explicit false means "no password yet" — old payloads (undefined) get the change flow.
  const setMode = member?.has_password === false;

  async function save() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      if (setMode) {
        await api.changePassword("", next);
        setMsg("Password set — email + password login now works."); setNext("");
        await refreshMember();
      } else {
        await api.changePassword(cur, next);
        setMsg("Password updated."); setCur(""); setNext("");
      }
    } catch (e: any) { setErr(e.message || (setMode ? "couldn't set password" : "couldn't change password")); }
    finally { setBusy(false); }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="mx-auto block text-[12px] text-subtle hover:text-teal">
        {setMode ? "Set a password" : "Change password"}
      </button>
    );
  }
  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="label">{setMode ? "Set a password" : "Change password"}</h2>
        <button onClick={() => setOpen(false)} className="text-[12px] text-subtle hover:text-ink">close</button>
      </div>
      <div className="mt-3 space-y-3">
        {setMode ? (
          <p className="text-[12.5px] text-muted">
            You currently sign in with Google — add a password to also log in with email + password. Google keeps working.
          </p>
        ) : (
          <input className="input w-full" type="password" placeholder="Current (or temporary) password"
            value={cur} onChange={(e) => { setCur(e.target.value); setErr(null); setMsg(null); }}
            autoComplete="current-password" />
        )}
        <input className="input w-full" type="password" placeholder="New password (8+ chars)"
          value={next} onChange={(e) => { setNext(e.target.value); setErr(null); setMsg(null); }}
          autoComplete="new-password" />
        <div className="flex items-center gap-2.5">
          <Button variant="ok" onClick={save} busy={busy} disabled={(!setMode && !cur) || next.length < 8}>
            {setMode ? "Set password" : "Update"}
          </Button>
          {msg && <span className="text-[12px] text-green">✓ {msg}</span>}
          {err && <span className="text-[12px] text-red">⚠ {err}</span>}
        </div>
      </div>
    </Card>
  );
}
