"use client";

// The one identity panel on Account: Name · Email · Password, three rows with
// one interaction idiom. Collapsed = the current value + a quiet Edit; open =
// the editor inline, Enter saves, Esc cancels, errors land under the field,
// a save collapses the row and leaves a short "saved" mark. Only one row is
// open at a time, so the card never grows into a form wall.
//
// Email keeps its two-step proof: the 6-digit code goes to the NEW address and
// members.email only moves once that code comes back, so the account can never
// end up pointing at an inbox nobody proved.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Member } from "@/lib/types";
import { Button, Card } from "./ui";

type RowKey = "name" | "email" | "password";

function RowShell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-white/10 py-3 first:border-t-0 first:pt-0 sm:grid sm:grid-cols-[84px_1fr] sm:gap-4">
      <div className="label pt-[3px]">{label}</div>
      <div className="mt-1.5 min-w-0 sm:mt-0">{children}</div>
    </div>
  );
}

function Shown({ value, action, onEdit, saved, field }: {
  value: string; action: string; onEdit: () => void; saved: boolean;
  /** What this row edits ("name"/"email"/"password") — the three rows otherwise
   *  expose three identical `button "Edit"` nodes to screen readers, with the
   *  field name only conveyed by visual adjacency. */
  field: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="min-w-0 flex-1 truncate text-[13.5px] text-ink">{value}</span>
      {saved && <span className="text-[12px] text-green">saved</span>}
      <button onClick={onEdit} aria-label={`${action} ${field}`}
        className="text-[12px] text-subtle transition hover:text-teal">
        {action}
      </button>
    </div>
  );
}

function Actions({ save, cancel, label, busy, disabled, err }: {
  save: () => void; cancel: () => void; label: string;
  busy: boolean; disabled: boolean; err: string | null;
}) {
  return (
    <>
      <div className="mt-2 flex flex-wrap items-center gap-2.5">
        <Button variant="ok" className="!px-3.5 !py-2 !text-[13px]" onClick={save} busy={busy} disabled={disabled}>
          {label}
        </Button>
        <button onClick={cancel} className="text-[12.5px] text-subtle transition hover:text-ink">cancel</button>
      </div>
      {err && <div className="mt-2 text-[12.5px] text-red">⚠ {err}</div>}
    </>
  );
}

const inputCls = "input !py-2 !text-[13px]";

export function Profile({ member, onMember }: { member: Member; onMember: (m: Member) => void }) {
  const { refreshMember } = useAuth();
  const [open, setOpen] = useState<RowKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<RowKey | null>(null);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");

  // Only an explicit false means "no password yet" — old payloads (undefined)
  // get the change flow, never the set flow.
  const noPassword = member.has_password === false;

  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (savedTimer.current) clearTimeout(savedTimer.current); }, []);

  function close() {
    setOpen(null); setErr(null); setBusy(false);
    setCode(""); setCodeSent(false); setCurPw(""); setNewPw("");
  }
  function done(row: RowKey) {
    close();
    setSaved(row);
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaved(null), 2600);
  }
  function edit(row: RowKey) {
    close();
    setSaved(null);
    setOpen(row);
    if (row === "name") setName(member.display_name);
    if (row === "email") setEmail("");
  }
  // Enter saves, Esc cancels — the same keys in all three editors.
  const keys = (save: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    else if (e.key === "Escape") close();
  };

  async function run(fn: () => Promise<void>, fallback: string) {
    if (busy) return;
    setBusy(true); setErr(null);
    try { await fn(); }
    catch (e: any) { setErr(e?.message || fallback); setBusy(false); }
  }

  const saveName = () => run(async () => {
    const v = name.trim();
    if (!v || v === member.display_name) { close(); return; }
    onMember((await api.renameDisplayName(v)).member);
    done("name");
  }, "couldn't save your name");

  const sendCode = () => run(async () => {
    await api.emailChangeStart(email.trim().toLowerCase());
    setCode(""); setCodeSent(true); setBusy(false);
  }, "couldn't send the code");

  const confirmEmail = () => run(async () => {
    onMember((await api.emailChangeConfirm(email.trim().toLowerCase(), code.trim())).member);
    done("email");
  }, "that code didn't work");

  const savePassword = () => run(async () => {
    await api.changePassword(noPassword ? "" : curPw, newPw);
    if (noPassword) await refreshMember();
    done("password");
  }, noPassword ? "couldn't set your password" : "couldn't change your password");

  return (
    <Card className="rise">
      <h2 className="label">Profile</h2>
      <div className="mt-3">
        <RowShell label="Name">
          {open === "name" ? (
            <>
              <input autoFocus value={name} maxLength={40} disabled={busy} aria-label="Display name"
                className={`${inputCls} !w-[min(260px,100%)]`}
                onChange={(e) => { setName(e.target.value); setErr(null); }}
                onKeyDown={keys(saveName)} />
              <Actions save={saveName} cancel={close} label="Save" busy={busy}
                disabled={!name.trim()} err={err} />
            </>
          ) : (
            <Shown value={member.display_name} action="Edit" field="name" saved={saved === "name"}
              onEdit={() => edit("name")} />
          )}
        </RowShell>

        <RowShell label="Email">
          {open === "email" ? (
            <>
              {!codeSent ? (
                <input autoFocus type="email" value={email} disabled={busy} aria-label="New email"
                  placeholder="new@email.com" spellCheck={false} autoComplete="email"
                  className={`${inputCls} !w-[min(260px,100%)]`}
                  onChange={(e) => { setEmail(e.target.value); setErr(null); }}
                  onKeyDown={keys(sendCode)} />
              ) : (
                <>
                  <p className="text-[12.5px] text-muted">
                    Code sent to <span className="text-ink">{email.trim().toLowerCase()}</span>
                  </p>
                  <input autoFocus value={code} disabled={busy} aria-label="Verification code"
                    placeholder="6-digit code" inputMode="numeric" maxLength={6} spellCheck={false}
                    autoComplete="one-time-code"
                    className={`${inputCls} tnum mt-1.5 !w-[130px]`}
                    onChange={(e) => { setCode(e.target.value.replace(/\D/g, "")); setErr(null); }}
                    onKeyDown={keys(confirmEmail)} />
                </>
              )}
              <Actions save={codeSent ? confirmEmail : sendCode} cancel={close}
                label={codeSent ? "Confirm" : "Send code"} busy={busy}
                disabled={codeSent ? code.length < 6 : !email.trim()} err={err} />
            </>
          ) : (
            <Shown value={member.email} action="Edit" field="email" saved={saved === "email"}
              onEdit={() => edit("email")} />
          )}
        </RowShell>

        <RowShell label="Password">
          {open === "password" ? (
            <>
              {noPassword && (
                <p className="mb-1.5 text-[12.5px] text-muted">
                  You sign in with Google — a password adds email login, and Google keeps working.
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                {!noPassword && (
                  <input autoFocus type="password" value={curPw} disabled={busy} aria-label="Current password"
                    placeholder="Current password" autoComplete="current-password"
                    className={`${inputCls} !w-[min(240px,100%)]`}
                    onChange={(e) => { setCurPw(e.target.value); setErr(null); }}
                    onKeyDown={keys(savePassword)} />
                )}
                <input autoFocus={noPassword} type="password" value={newPw} disabled={busy} aria-label="New password"
                  placeholder="New password (8+ chars)" autoComplete="new-password"
                  className={`${inputCls} !w-[min(240px,100%)]`}
                  onChange={(e) => { setNewPw(e.target.value); setErr(null); }}
                  onKeyDown={keys(savePassword)} />
              </div>
              <Actions save={savePassword} cancel={close}
                label={noPassword ? "Set password" : "Save"} busy={busy}
                disabled={newPw.length < 8 || (!noPassword && !curPw)} err={err} />
            </>
          ) : (
            <Shown value={noPassword ? "Not set" : "••••••••"} action={noPassword ? "Set" : "Edit"} field="password"
              saved={saved === "password"} onEdit={() => edit("password")} />
          )}
        </RowShell>
      </div>
    </Card>
  );
}
