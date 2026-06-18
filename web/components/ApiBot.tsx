"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { ApiKey, ResearchResult } from "@/lib/types";

const PUBLIC_URL = "https://concord.5.78.193.177.sslip.io";

function money(n?: number | null) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
function fmtTime(epoch?: number | null) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function KeyGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden>
      <circle cx="7.5" cy="15.5" r="4.5" /><path d="M11 12l8.5-8.5" /><path d="M16 7l3 3" />
    </svg>
  );
}

/** Masked secret with Show / Copy. */
export function SecretField({ value }: { value: string }) {
  const [show, setShow] = useState(false);
  const [copied, setCopied] = useState(false);
  const masked = value.slice(0, 8) + " •••••••••• " + value.slice(-4);
  return (
    <div className="glass-2 flex items-center gap-2 rounded-xl border border-hair px-3 py-2.5">
      <code className="flex-1 truncate font-mono text-[14px] tracking-wide text-ink">{show ? value : masked}</code>
      <button className="btn btn-glass !px-3 !py-1.5 !text-[12px]"
        onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>
        {copied ? "Copied" : "Copy"}
      </button>
      <button className="btn btn-glass !px-3 !py-1.5 !text-[12px]" onClick={() => setShow((s) => !s)}>
        {show ? "Hide" : "Show"}
      </button>
    </div>
  );
}

/** Compact "Vote via API" card for the vote page. */
export function VoteApiCard() {
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    setCreating(true); setErr(null);
    try { setFresh((await api.createApiKey("vote bot")).key); }
    catch (e: any) { setErr(e?.message || "could not create key"); }
    finally { setCreating(false); }
  }

  return (
    <div className="glass rise p-5 sm:p-6">
      <div className="flex items-start gap-3.5">
        <span className="mark !h-[44px] !w-[44px] !rounded-[13px]"><KeyGlyph /></span>
        <div>
          <h3 className="font-display text-[18px] font-semibold leading-tight">Vote via API</h3>
          <p className="mt-0.5 text-[13px] text-muted">Let a bot cast your vote — same power, same path.</p>
        </div>
      </div>

      <div className="mt-5">
        <div className="label mb-2">Secret key</div>
        {fresh ? (
          <>
            <SecretField value={fresh} />
            <p className="mt-2 text-[12px] text-subtle">Save it now. The key is shown once and cannot be retrieved later.</p>
          </>
        ) : (
          <button className="btn btn-go" disabled={creating} onClick={create}>
            {creating ? "Creating…" : "Create API key"}
          </button>
        )}
        {err && <p className="mt-2 text-[12px] text-red">{err}</p>}
      </div>

      <p className="mt-4 text-[12px] text-subtle">
        Live endpoints: <span className="font-mono text-muted">/vote · /universe · /me · /program</span>. Manage &amp; revoke in{" "}
        <Link href="/program" className="text-teal hover:underline">API bot</Link>.
      </p>
    </div>
  );
}

/** Full key list + create + revoke, for the API bot page. */
export function KeyManager() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("research voter");
  const [fresh, setFresh] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setKeys((await api.apiKeys()).keys); } catch { /* ignore */ } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setCreating(true);
    try { setFresh((await api.createApiKey(name)).key); await load(); }
    finally { setCreating(false); }
  }
  async function revoke(id: number) {
    if (!confirm("Revoke this key? Any program using it stops working.")) return;
    await api.revokeApiKey(id); await load();
  }

  return (
    <div className="glass rise p-5 sm:p-6">
      <div className="label">API keys</div>
      <p className="mt-1 text-[13px] text-muted">
        Mint a key, then point the research-voter (or any program) at it. The full key is shown once.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2.5">
        <div>
          <div className="label mb-1.5 !text-[10px]">Label</div>
          <input className="input !w-56" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
        </div>
        <button className="btn btn-go" disabled={creating} onClick={create}>
          {creating ? "Creating…" : "Create key"}
        </button>
      </div>

      {fresh && (
        <div className="mt-4">
          <div className="label mb-2 !text-teal">New key — copy it now</div>
          <SecretField value={fresh} />
          <p className="mt-2 text-[12px] text-subtle">Shown once and cannot be retrieved later.</p>
        </div>
      )}

      <div className="mt-5">
        {loading ? (
          <p className="text-[13px] text-subtle">loading…</p>
        ) : keys.length === 0 ? (
          <p className="text-[13px] text-subtle">No keys yet — create one above.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {keys.map((k) => (
              <div key={k.id} className={`flex items-center gap-3 rounded-xl border border-hair px-3 py-2.5 ${k.revoked_at ? "opacity-40" : ""}`}>
                <span className="font-mono text-[13px] text-ink">{k.prefix}…</span>
                <span className="text-[12.5px] text-muted">{k.name}</span>
                <span className="ml-auto text-[11.5px] text-subtle">
                  {k.revoked_at ? "revoked" : k.last_used_at ? `used ${fmtTime(k.last_used_at)}` : `made ${fmtTime(k.created_at)}`}
                </span>
                {!k.revoked_at && (
                  <button className="btn btn-ghost !px-2 !py-1 !text-[12px] hover:!text-red" onClick={() => revoke(k.id)}>Revoke</button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Run the research-voter (preview / cast) and show the resulting basket. */
export function ResearchVoterCard() {
  const [busy, setBusy] = useState<string | null>(null);
  const [res, setRes] = useState<ResearchResult | null>(null);

  async function run(dry: boolean) {
    setBusy(dry ? "preview" : "cast");
    try { setRes(dry ? await api.researchPreview() : await api.researchVote(false)); }
    catch (e: any) { setRes({ source: "error", research_count: 0, eligible: [], excluded: [], allocations: [], dry_run: dry, error: e?.message || "failed" }); }
    finally { setBusy(null); }
  }

  const allocs = res?.allocations || [];
  const maxW = Math.max(1, ...allocs.map((a) => a.weight));

  return (
    <div className="glass rise p-5 sm:p-6">
      <div className="label">Research voter</div>
      <p className="mt-1 text-[13px] text-muted">
        Reads your Robinhood watchlists, keeps the S&amp;P 500 names, takes the top 20, and votes with a
        conviction-weighted split — a name in more of your lists gets more weight (capped ~12%).
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <button className="btn btn-glass" disabled={!!busy} onClick={() => run(true)}>
          {busy === "preview" ? "Reading…" : "Preview"}
        </button>
        <button className="btn btn-go" disabled={!!busy} onClick={() => run(false)}>
          {busy === "cast" ? "Voting…" : "Run & cast vote"}
        </button>
      </div>

      {res && (
        <div className="mt-5">
          {res.error && <p className="mb-3 text-[12.5px] text-amber">{res.error}</p>}
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11.5px] text-subtle">
            <span>source: <b className="text-ink">{res.source}</b></span>
            <span>research: {res.research_count}</span>
            <span>basket: {allocs.length}</span>
            <span className={res.dry_run ? "pill" : "pill pill-ok"}>{res.dry_run ? "preview" : "vote cast"}</span>
          </div>
          {allocs.length > 0 && (
            <div className="flex flex-col gap-2">
              {allocs.map((a) => (
                <div key={a.symbol} className="flex items-center gap-2.5">
                  <span className="w-14 flex-none font-display text-[13px] font-semibold">{a.symbol}</span>
                  <div className="bar flex-1"><i style={{ width: `${(a.weight / maxW) * 100}%` }} /></div>
                  <span className="w-12 flex-none text-right text-[12px] tnum text-muted">{a.weight}%</span>
                </div>
              ))}
            </div>
          )}
          {res.excluded.length > 0 && (
            <p className="mt-3 text-[11px] text-subtle">Excluded (not in S&amp;P 500): {res.excluded.join(", ")}</p>
          )}
        </div>
      )}
    </div>
  );
}

/** The copy-paste snippet for running the bot externally. */
export function ConnectProgram() {
  const [copied, setCopied] = useState(false);
  const snippet = `export CONCORD_API_KEY=ck_live_...          # your key from above
export CONCORD_API_URL=${PUBLIC_URL}

python research_voter.py            # cast today's vote
python research_voter.py --dry      # preview only`;
  return (
    <div className="glass rise p-5 sm:p-6">
      <div className="label">Connect a program</div>
      <p className="mt-1 text-[13px] text-muted">
        Point any program at your key. The bundled research-voter is ready for cron — it calls the same endpoint as the button above.
      </p>
      <div className="relative mt-4">
        <pre className="glass-2 overflow-x-auto whitespace-pre rounded-xl border border-hair p-4 font-mono text-[12px] text-muted">{snippet}</pre>
        <button className="btn btn-glass absolute right-2 top-2 !px-3 !py-1.5 !text-[12px]"
          onClick={() => { navigator.clipboard?.writeText(snippet); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-3 text-[11.5px] text-subtle">
        <span className="font-mono">POST {PUBLIC_URL}/api/program/research-vote</span> · header{" "}
        <span className="font-mono">Authorization: Bearer ck_live_…</span> · body <span className="font-mono">{"{ dry_run, symbols? }"}</span>.
      </p>
    </div>
  );
}
