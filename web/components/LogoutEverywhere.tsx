"use client";

// "Log out everywhere" — the quiet companion to Change password on Account.
// POST /me/logout-all bumps session_version server-side (every session on
// every device dies, this one included), then we hard-logout locally. Inline
// confirm, no modal: destructive but instantly recoverable by logging back in.

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function LogoutEverywhere() {
  const { logout } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!confirming) {
    return (
      <button onClick={() => { setErr(null); setConfirming(true); }}
        className="mx-auto block text-[12px] text-subtle hover:text-teal">
        Log out everywhere
      </button>
    );
  }
  return (
    <div className="text-center text-[12px]">
      <span className="text-muted">Log out of every device, including this one?</span>
      <div className="mt-1.5 flex items-center justify-center gap-3">
        <button disabled={busy}
          className="font-semibold text-red hover:underline disabled:opacity-50"
          onClick={async () => {
            setBusy(true); setErr(null);
            try { await api.logoutAll(); logout(); }
            catch (e: any) {
              setErr(e?.message || "couldn't log out everywhere");
              setBusy(false);
            }
            // on success the local logout unmounts this component — no cleanup
          }}>
          {busy ? "logging out…" : "Yes, log out everywhere"}
        </button>
        <button onClick={() => { setConfirming(false); setErr(null); }}
          className="text-subtle hover:text-ink">cancel</button>
      </div>
      {err && <div className="mt-1.5 text-red">⚠ {err}</div>}
    </div>
  );
}
