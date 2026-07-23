"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ApiKey } from "@/lib/types";

const PUBLIC_URL = "https://concord.5.78.193.177.sslip.io";

function fmtTime(epoch?: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function SecretField({ value }: { value: string }) {
  const [show, setShow] = useState(false);
  const [copied, setCopied] = useState(false);
  const masked = value.slice(0, 8) + " •••••••••• " + value.slice(-4);
  return (
    <div className="glass-2 flex items-center gap-2 rounded-xl border border-hair px-3 py-2.5">
      <code className="flex-1 truncate font-mono text-[13.5px] tracking-wide text-ink">{show ? value : masked}</code>
      <button className="btn btn-glass !px-3 !py-1.5 !text-[12px]"
        onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>
        {copied ? "Copied" : "Copy"}
      </button>
      <button className="btn btn-glass !px-3 !py-1.5 !text-[12px]" onClick={() => setShow((s) => !s)}>{show ? "Hide" : "Show"}</button>
    </div>
  );
}

/** Anyone can generate their own key here to connect their own voting bot. */
export function MemberKeys() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("my bot");
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setKeys((await api.apiKeys()).keys); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setCreating(true); setErr(null);
    try { setFresh((await api.createApiKey(name)).key); await load(); }
    catch (e: any) { setErr(e?.message || "couldn't create the key — please try again."); }
    finally { setCreating(false); }
  }
  async function revoke(id: number) {
    if (!confirm("Revoke this key? Any bot using it stops working.")) return;
    await api.revokeApiKey(id); await load();
  }

  return (
    <div className="glass shine rise p-5 sm:p-6">
      <div className="flex items-center justify-between">
        <span className="label !text-teal">Vote via API</span>
        <span className="text-[11.5px] text-subtle">connect your own bot</span>
      </div>
      <p className="mt-1.5 text-[13px] text-muted">
        Generate a key, then have your own program cast your daily vote — same voting power, same ballot, no money moved.
        The full key is shown once.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2.5">
        <div>
          <div className="label mb-1.5 !text-[10px]">Label</div>
          <input className="input !w-48" value={name} onChange={(e) => { setName(e.target.value); setErr(null); }} maxLength={60} />
        </div>
        <button className="btn btn-go" disabled={creating} onClick={create}>{creating ? "Creating…" : "Generate key"}</button>
      </div>
      {err && <div className="mt-2 text-[12px] text-red">⚠ {err}</div>}

      {fresh && (
        <div className="mt-4">
          <div className="label mb-2 !text-teal">New key — copy it now</div>
          <SecretField value={fresh} />
          <p className="mt-2 text-[12px] text-subtle">Shown once and cannot be retrieved later.</p>
        </div>
      )}

      {keys.some((k) => !k.revoked_at) && (
        <div className="mt-5 flex flex-col gap-1.5">
          {keys.filter((k) => !k.revoked_at).map((k) => (
            <div key={k.id} className="flex items-center gap-3 rounded-xl border border-hair px-3 py-2.5">
              <span className="font-mono text-[13px] text-ink">{k.prefix}…</span>
              <span className="text-[12.5px] text-muted">{k.name}</span>
              <span className="ml-auto text-[11.5px] text-subtle">
                {k.last_used_at ? `used ${fmtTime(k.last_used_at)}` : `made ${fmtTime(k.created_at)}`}
              </span>
              <button className="btn btn-ghost !px-2 !py-1 !text-[12px] hover:!text-red" onClick={() => revoke(k.id)}>Revoke</button>
            </div>
          ))}
        </div>
      )}

      <p className="mt-4 text-[11.5px] text-subtle">
        Point your bot at <span className="font-mono text-muted">POST {PUBLIC_URL}/api/program/research-vote</span> with{" "}
        <span className="font-mono text-muted">Authorization: Bearer ck_live_…</span>
      </p>
    </div>
  );
}
