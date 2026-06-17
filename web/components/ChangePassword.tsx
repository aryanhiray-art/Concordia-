"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button, Card } from "./ui";

export function ChangePassword() {
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await api.changePassword(cur, next);
      setMsg("Password updated."); setCur(""); setNext("");
    } catch (e: any) { setErr(e.message || "couldn't change password"); }
    finally { setBusy(false); }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="mx-auto block text-[12px] text-subtle hover:text-teal">
        Change password
      </button>
    );
  }
  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="label">Change password</h2>
        <button onClick={() => setOpen(false)} className="text-[12px] text-subtle hover:text-ink">close</button>
      </div>
      <div className="mt-3 space-y-3">
        <input className="input w-full" type="password" placeholder="Current (or temporary) password"
          value={cur} onChange={(e) => { setCur(e.target.value); setErr(null); setMsg(null); }}
          autoComplete="current-password" />
        <input className="input w-full" type="password" placeholder="New password (8+ chars)"
          value={next} onChange={(e) => { setNext(e.target.value); setErr(null); setMsg(null); }}
          autoComplete="new-password" />
        <div className="flex items-center gap-2.5">
          <Button variant="ok" onClick={save} busy={busy} disabled={!cur || next.length < 8}>Update</Button>
          {msg && <span className="text-[12px] text-green">✓ {msg}</span>}
          {err && <span className="text-[12px] text-red">⚠ {err}</span>}
        </div>
      </div>
    </Card>
  );
}
