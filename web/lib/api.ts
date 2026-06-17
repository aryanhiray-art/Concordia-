"use client";

import type { Member, SessionView, UniverseItem, ExecutionRow, PowerView, Account } from "./types";

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

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(opts.headers as any) };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const res = await fetch(`${BASE}${path}`, { ...opts, headers, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  base: BASE,
  // auth — invite code + email + a password you create; log in with email + password
  signup: (invite_code: string, email: string, password: string, display_name: string) =>
    req<{ token: string; member: Member }>("/api/auth/signup", {
      method: "POST", body: JSON.stringify({ invite_code, email, password, display_name }),
    }),
  login: (email: string, password: string) =>
    req<{ token: string; member: Member }>("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),
  me: () => req<{ member: Member; power: PowerView }>("/api/me"),
  changePassword: (current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/api/me/password", {
      method: "PUT", body: JSON.stringify({ current_password, new_password }),
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
  setAllocation: (type: "percent" | "dollar", value: number) =>
    req<Account>("/api/me/allocation", { method: "PUT", body: JSON.stringify({ type, value }) }),

  // voting
  searchUniverse: (q: string, limit = 40) =>
    req<{ symbols: UniverseItem[] }>(`/api/universe?q=${encodeURIComponent(q)}&limit=${limit}`),
  voteToday: () => req<SessionView>("/api/vote/today"),
  // Concordia model: allocate % of voting power across picks (sums to ≤100)
  castVote: (allocations: { symbol: string; weight: number }[]) =>
    req<SessionView>("/api/vote/today", { method: "POST", body: JSON.stringify({ allocations }) }),
  results: () => req<{ session: SessionView | null; executions: ExecutionRow[] }>("/api/vote/results"),
  // (owner/admin controls live on the forge dashboard, not in this member app)
};
