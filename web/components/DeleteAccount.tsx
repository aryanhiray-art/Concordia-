"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Card } from "./ui";

// The exit right terms §7 promises: self-service, immediate, total.
export function DeleteAccount() {
  const { member, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isOwner = member?.role === "owner";

  async function doDelete() {
    setBusy(true); setErr(null);
    try {
      await api.deleteAccount(confirm.trim());
      logout();                       // session is already dead server-side
    } catch (e: any) {
      setErr(e?.message || "couldn't delete the account");
    } finally { setBusy(false); }
  }

  return (
    <Card>
      <h2 className="label">Leave the club</h2>
      {!open ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[12.5px] leading-relaxed text-subtle">
            Deleting your account stops all trading for you instantly, destroys the club&apos;s
            Robinhood access and your logins, and erases your personal details.
          </p>
          <button onClick={() => setOpen(true)} className="text-[13px] font-semibold text-red hover:underline">
            Delete account…
          </button>
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <p className="text-[12.5px] leading-relaxed text-muted">
            This is immediate and permanent. <b className="text-ink">Your positions are not sold</b>{" "}
            — they stay in your own Robinhood account, which was always yours. The club keeps only
            its anonymized books (votes and orders with your identity removed) and the record that
            you accepted the agreement.
            {isOwner && (
              <span className="mt-1 block text-amber">
                The owner account can&apos;t delete itself — transfer ownership first.
              </span>
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2.5">
            <input
              value={confirm}
              onChange={(e) => { setConfirm(e.target.value); setErr(null); }}
              placeholder='type DELETE'
              className="input !w-36 !py-1.5 font-mono !text-[12px]"
              spellCheck={false} autoComplete="off"
            />
            <Button variant="ok" onClick={doDelete} busy={busy}
              disabled={isOwner || confirm.trim().toUpperCase() !== "DELETE"}>
              Permanently delete
            </Button>
            <button onClick={() => { setOpen(false); setConfirm(""); setErr(null); }}
              className="text-[12.5px] text-subtle hover:text-ink">cancel</button>
          </div>
          {err && <div className="text-[13px] text-red">⚠ {err}</div>}
        </div>
      )}
    </Card>
  );
}
