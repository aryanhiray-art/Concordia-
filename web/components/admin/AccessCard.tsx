"use client";

// Who can get in, and with what. The club's invite code lives HERE — the owner
// asked for it to be readable in the console, not buried in the .env — and the
// card also states the rule the sign-in page embodies: the ops console itself
// has NO code (operator accounts sign in with email + password; roles are
// granted, never self-served). Demo and beta are separate instances with their
// own codes on their own consoles, so this card only ever shows its own.

import { useEffect, useState } from "react";
import { api, type AdminInvite, type AdminOverview } from "@/lib/api";
import { OpsCard, SectionLabel } from "./util";

export function AccessCard({ overview, err }: { overview: AdminOverview | null; err: string | null }) {
  const [copied, setCopied] = useState<null | "ok" | "fail">(null);
  const code = overview?.invite_code || null;

  async function copy() {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied("ok");
    } catch {
      // clipboard API needs a secure context + permission; fall back to the
      // execCommand path before telling the operator to do it by hand
      try {
        const ta = document.createElement("textarea");
        ta.value = code;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        setCopied(ok ? "ok" : "fail");
      } catch {
        setCopied("fail");
      }
    }
    setTimeout(() => setCopied(null), 1600);
  }

  return (
    <OpsCard>
      <SectionLabel>Access</SectionLabel>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-[12px] text-muted">Invite code</span>
        {code ? (
          <button onClick={copy} title="Copy invite code"
            className="rounded-[9px] border border-teal/30 bg-teal/[0.07] px-2.5 py-1 font-mono text-[12.5px] font-semibold tracking-wide text-ink transition hover:border-teal/50">
            {code}
          </button>
        ) : (
          <span className="text-[12px] text-subtle">{err ? "unavailable — overview failed" : "…"}</span>
        )}
        {copied && (
          <span className={`text-[11.5px] ${copied === "ok" ? "text-green" : "text-amber"}`}>
            {copied === "ok" ? "copied" : "copy failed — select it manually"}
          </span>
        )}
      </div>
      <p className="mt-2 text-[11.5px] leading-snug text-subtle">
        for new-member signup at voteconcordia.com — this console has no code
        (operator roles are granted per account)
      </p>
      <MintedInvites />
    </OpsCard>
  );
}

// Minted per-person/per-batch codes: expiring, use-limited, revocable — the
// answer to "the shared code leaked into a group chat". Lives inside Access
// because that's where the operator already looks for admission.
function MintedInvites() {
  const [rows, setRows] = useState<AdminInvite[] | null>(null);
  const [note, setNote] = useState("");
  const [days, setDays] = useState<string>("14");
  const [uses, setUses] = useState<string>("1");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api.adminInvites().then((r) => setRows(r.invites)).catch(() => setRows([]));
  useEffect(() => { load(); }, []);

  async function mint() {
    setBusy(true); setErr(null);
    try {
      await api.adminInviteMint({
        note: note.trim() || undefined,
        expires_days: days.trim() ? Number(days) : null,
        max_uses: uses.trim() ? Number(uses) : null,
      });
      setNote(""); load();
    } catch (e: any) { setErr(e?.message || "couldn't mint the invite"); }
    finally { setBusy(false); }
  }

  async function revoke(id: number) {
    try { await api.adminInviteRevoke(id); load(); } catch { /* row refresh shows truth */ }
  }

  const fmtWhen = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString() : "—");

  return (
    <div className="mt-4 border-t border-white/10 pt-3.5">
      <div className="mb-2 text-[12px] font-semibold text-muted">Minted invites</div>
      <div className="flex flex-wrap items-center gap-2">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (who is this for?)"
          className="input !w-[190px] !py-1.5 !text-[12px]" />
        <label className="flex items-center gap-1 text-[11.5px] text-subtle">
          expires
          <input value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))}
            className="input !w-[52px] !py-1.5 !text-[12px] tnum" aria-label="expires in days" />
          d
        </label>
        <label className="flex items-center gap-1 text-[11.5px] text-subtle">
          uses
          <input value={uses} onChange={(e) => setUses(e.target.value.replace(/\D/g, ""))}
            className="input !w-[52px] !py-1.5 !text-[12px] tnum" aria-label="max uses" />
        </label>
        <button onClick={mint} disabled={busy}
          className="rounded-[9px] border border-teal/30 bg-teal/[0.07] px-3 py-1.5 text-[12px] font-semibold text-teal transition hover:border-teal/50 disabled:opacity-50">
          {busy ? "minting…" : "Mint code"}
        </button>
      </div>
      {err && <div className="mt-1.5 text-[11.5px] text-red">⚠ {err}</div>}
      {rows === null ? (
        <div className="mt-2 text-[11.5px] text-subtle">…</div>
      ) : rows.length === 0 ? (
        <div className="mt-2 text-[11.5px] text-subtle">none minted yet — the shared code above still admits</div>
      ) : (
        <div className="mt-2 max-h-[240px] space-y-1.5 overflow-y-auto pr-1">
          {rows.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px]">
              <button onClick={() => navigator.clipboard?.writeText(r.code).catch(() => {})}
                title="Copy code"
                className={`rounded-[7px] border px-2 py-0.5 font-mono text-[11.5px] font-semibold transition ${r.active ? "border-teal/30 bg-teal/[0.06] text-ink hover:border-teal/50" : "border-white/10 text-subtle line-through"}`}>
                {r.code}
              </button>
              <span className="text-subtle">{r.note || "—"}</span>
              <span className="tnum text-subtle">{r.uses}{r.max_uses != null ? `/${r.max_uses}` : ""} used</span>
              <span className="tnum text-subtle">exp {fmtWhen(r.expires_at)}</span>
              {r.revoked_at ? (
                <span className="text-[11px] text-red">revoked</span>
              ) : r.active ? (
                <button onClick={() => revoke(r.id)}
                  className="text-[11px] text-subtle underline-offset-2 transition hover:text-red hover:underline">
                  revoke
                </button>
              ) : (
                <span className="text-[11px] text-amber">inactive</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
