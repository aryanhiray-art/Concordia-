"use client";

import type { Member, SessionView, UniverseItem, ExecutionRow, PowerView, Account, ApiKey, ResearchResult, PerfView, AdminPanel } from "./types";

// Empty base = same-origin (Vercel rewrites /api + /oauth to the forge backend).
// Local dev sets NEXT_PUBLIC_CONCORD_API=http://localhost:8780.
const BASE = (process.env.NEXT_PUBLIC_CONCORD_API || "").replace(/\/$/, "");
const TOKEN_KEY = "concord_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (typeof window === "undefined") return;
  if (t) window.localStorage.setItem(TOKEN_KEY, t);
  else window.localStorage.removeItem(TOKEN_KEY);
}

// HTTP-level failure: carries the status so callers can tell a bad token (401/403)
// from a backend blip (5xx). Network failures (fetch itself throwing) stay plain
// TypeErrors with no .status — treat those like a blip too.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(opts.headers as any) };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const res = await fetch(`${BASE}${path}`, { ...opts, headers, cache: "no-store" });
  if (!res.ok) {
    // FastAPI errors: `detail` is a string for app errors, but a LIST of
    // {loc, msg, …} objects for 422 validation errors — flatten either into a
    // human sentence (never "[object Object]").
    let detail = res.statusText;
    try {
      const d = (await res.json()).detail;
      if (typeof d === "string" && d) detail = d;
      else if (Array.isArray(d)) {
        detail = d.map((x: any) => {
          const field = Array.isArray(x?.loc) ? String(x.loc[x.loc.length - 1]) : "";
          const msg = typeof x?.msg === "string" ? x.msg.replace(/^Value error, /, "") : "invalid value";
          return field && field !== "body" ? `${field}: ${msg}` : msg;
        }).join(" · ");
      } else if (d && typeof d === "object") detail = (d as any).msg || JSON.stringify(d);
    } catch {}
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  base: BASE,
  // auth — invite code + email + a password you create; log in with email + password.
  // consent = the club-agreement checkbox (required for new signups in prod).
  // signupStart: pre-flight that validates the invite and (if email verification is on)
  // sends a 6-digit code — signup then needs that code in email_code.
  // demo_code arrives ONLY from the demo instance (no real inbox there — the
  // UI shows/prefills the code); production never includes it.
  signupStart: (invite_code: string, email: string) =>
    req<{ code_required: boolean; demo_code?: string }>("/api/auth/signup/start", {
      method: "POST", body: JSON.stringify({ invite_code, email }),
    }),
  signup: (invite_code: string, email: string, password: string, display_name: string, consent?: boolean, email_code?: string) =>
    req<{ token: string; member: Member }>("/api/auth/signup", {
      method: "POST", body: JSON.stringify({ invite_code, email, password, display_name, consent, email_code }),
    }),
  // self-serve password reset — forgot always 200s (no account enumeration);
  // email_enabled=false means the club can't send mail → fall back to ask-the-owner.
  forgot: (email: string) =>
    req<{ ok: boolean; email_enabled: boolean }>("/api/auth/forgot", {
      method: "POST", body: JSON.stringify({ email }),
    }),
  resetPassword: (email: string, code: string, new_password: string) =>
    req<{ ok: boolean }>("/api/auth/reset", {
      method: "POST", body: JSON.stringify({ email, code, new_password }),
    }),
  login: (email: string, password: string) =>
    req<{ token: string; member: Member }>("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),
  // Google sign-in — credential = the Google ID token from Identity Services.
  // Existing members just log in; new members must also send invite_code + consent.
  googleLogin: (credential: string, invite_code?: string, consent?: boolean) =>
    req<{ token: string; member: Member }>("/api/auth/google", {
      method: "POST", body: JSON.stringify({ credential, invite_code, consent }),
    }),
  me: () => req<{ member: Member; power: PowerView }>("/api/me"),
  deleteAccount: (confirm: string) =>
    req<{ ok: boolean }>("/api/me", { method: "DELETE", body: JSON.stringify({ confirm }) }),
  changePassword: (current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/api/me/password", {
      method: "PUT", body: JSON.stringify({ current_password, new_password }),
    }),
  // legacy-member email confirmation (the ConfirmEmail interstitial) —
  // start emails a 6-digit code (429 when rate-limited, 4xx/5xx detail when the
  // send fails); verify flips email_verified server-side and returns the fresh member.
  verifyEmailStart: () =>
    req<{ sent: boolean; already_verified?: boolean }>("/api/me/verify-email/start", { method: "POST" }),
  verifyEmail: (code: string) =>
    req<{ ok: boolean; member: Member }>("/api/me/verify-email", {
      method: "POST", body: JSON.stringify({ code }),
    }),

  // robinhood — start, then paste back the redirect URL Robinhood sends you
  rhStart: () => req<{ authorize_url: string }>("/api/oauth/robinhood/start"),
  rhComplete: (redirect_url: string) =>
    req<{ ok: boolean; account_number: string | null; agentic_allowed: boolean }>(
      "/api/oauth/robinhood/complete", { method: "POST", body: JSON.stringify({ redirect_url }) }),
  rhDisconnect: () => req<{ ok: boolean }>("/api/oauth/robinhood/disconnect", { method: "POST" }),

  // the single on/off switch
  participate: (on: boolean) =>
    req<{ member: Member }>("/api/me/participate", { method: "PUT", body: JSON.stringify({ on }) }),

  // agentic balance + allocation (% or $) + club-only P&L
  account: () => req<Account>("/api/me/account"),
  // is the RH account cleared to place buys (investing-goals questionnaire done)?
  tradeReadiness: (force = false) =>
    req<{ connected: boolean; ready: boolean; reason: string; detail?: string }>(
      `/api/me/trade-readiness${force ? "?force=true" : ""}`),
  // the member's live positions (for the overview donut + holdings list)
  holdings: () =>
    req<{ holdings: { symbol: string; quantity: number; value: number; pnl: number | null }[] }>("/api/me/holdings"),
  setAllocation: (type: "percent" | "dollar", value: number) =>
    req<Account>("/api/me/allocation", { method: "PUT", body: JSON.stringify({ type, value }) }),
  // club decisions vs SPY, both indexed to 100 — club-level, identical for every member
  clubPerformance: () => req<PerfView>("/api/club/performance"),

  // voting
  searchUniverse: (q: string, limit = 40) =>
    req<{ symbols: UniverseItem[] }>(`/api/universe?q=${encodeURIComponent(q)}&limit=${limit}`),
  voteToday: () => req<SessionView>("/api/vote/today"),
  // Concordia model: allocate % of voting power across picks (sums to ≤100)
  castVote: (allocations: { symbol: string; weight: number }[]) =>
    req<SessionView>("/api/vote/today", { method: "POST", body: JSON.stringify({ allocations }) }),
  results: () => req<{
    session: SessionView | null; executions: ExecutionRow[];
    last_decided?: { trade_date: string; picks: { symbol: string; name?: string; weight?: number }[] } | null;
  }>("/api/vote/results"),

  // API keys (session login required) — for an external program / bot to vote for you
  apiKeys: () => req<{ keys: ApiKey[] }>("/api/me/api-keys"),
  createApiKey: (name: string) =>
    req<{ id: number; name: string; prefix: string; key: string; created_at: number }>(
      "/api/me/api-keys", { method: "POST", body: JSON.stringify({ name }) }),
  revokeApiKey: (id: number) =>
    req<{ ok: boolean }>(`/api/me/api-keys/${id}`, { method: "DELETE" }),

  // research-voter program — reads your Robinhood watchlists → S&P 500 top-20 → vote
  researchPreview: () => req<ResearchResult>("/api/program/research-preview"),
  researchVote: (dry = false) =>
    req<ResearchResult>("/api/program/research-vote", {
      method: "POST", body: JSON.stringify({ dry_run: dry }),
    }),
  // admin console (admin.voteconcordia.com) — read-only ops snapshot.
  // Session-auth, owner/admin only; plain members get a 403 whose detail
  // ("admin access required") surfaces through the normal ApiError path.
  adminPanel: () => req<AdminPanel>("/api/admin/panel"),
  // the console's ONLY mutation — arming lets real orders happen; audited server-side
  adminArm: (armed: boolean) =>
    req<{ armed: boolean }>("/api/admin/arm", { method: "POST", body: JSON.stringify({ armed }) }),
  // (owner/admin MUTATIONS stay off this app on purpose — arming real-money
  // trading is a forge-dashboard/CLI action, never a web button here)
};
