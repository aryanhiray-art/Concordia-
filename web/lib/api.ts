"use client";

import type { Member, SessionView, UniverseItem, ExecutionRow, PowerView, Account, HoldingsView, ApiKey, ResearchResult, PerfView, AdminPanel, AdminMemberRow, ClubHistoryView, MyVotesView, StockDetail, StockHistory, StockRangeKey, StockSearchResult, EquityHistory, EquityRangeKey } from "./types";

// Empty base = same-origin (Vercel rewrites /api + /oauth to the forge backend).
// Local dev sets NEXT_PUBLIC_CONCORD_API=http://localhost:8780.
const BASE = (process.env.NEXT_PUBLIC_CONCORD_API || "").replace(/\/$/, "");
const TOKEN_KEY = "concord_token";
const ACTIVITY_KEY = "concord_last_activity";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (typeof window === "undefined") return;
  if (t) { window.localStorage.setItem(TOKEN_KEY, t); markActivity(); }
  else { window.localStorage.removeItem(TOKEN_KEY); clearActivity(); }
}

// ── idle-logout bookkeeping (the policy itself lives in lib/auth.tsx) ──────
// The stamp is in localStorage, not a ref, for two reasons: every tab shares
// one clock (a buried tab must not log a member out while another tab is in
// use), and a reload can't launder away hours of inactivity. Fresh credentials
// (setToken above) count as activity, so logging back in never inherits an old
// stamp. Wrapped in try/catch — a blocked localStorage must not break auth.
export function markActivity(at: number = Date.now()) {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(ACTIVITY_KEY, String(at)); } catch {}
}
export function readActivity(): number | null {
  if (typeof window === "undefined") return null;
  try {
    const v = Number(window.localStorage.getItem(ACTIVITY_KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch { return null; }
}
export function clearActivity() {
  if (typeof window === "undefined") return;
  try { window.localStorage.removeItem(ACTIVITY_KEY); } catch {}
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

// ── Admin console payload shapes (admin.voteconcordia.com only) ────────────
// Colocated with their helpers instead of lib/types.ts on purpose: no
// member-facing surface imports these, and the console reads every field
// defensively — a web deploy can land before the backend that serves
// /api/admin/schedule and /api/admin/bots, so panels must degrade, not crash.

/** panel() member rows gained acct_type (kv `acct_type:{id}`); accuracy/P&L
 *  are speculative extras. All optional so the console renders "—" against a
 *  backend that predates them. voting_power has ALWAYS been in the payload
 *  (adminview.member_rows) — lib/types.AdminMemberRow just never declared it
 *  because the old console only read power via money.per_member. */
export type AdminMemberRowPlus = AdminMemberRow & {
  voting_power?: number | null;
  acct_type?: string | null;
  accuracy?: number | null;
  pnl?: number | null;
};

/** GET /api/admin/schedule — the split-cycle calendar the executor honours.
 *  "idle" is the backend's rendering of cycle_phase() returning None. */
export type AdminPhase = "sell" | "buy" | "both" | "idle";
export type AdminSchedule = {
  cadence: string;
  today: { date: string; phase: AdminPhase };
  upcoming: { date: string; weekday: string; phase: AdminPhase }[];
};

/** GET /api/admin/bots — bot roster + a cheap LLM reachability probe.
 *  accuracy = the last scored weekly return as a fraction (0.024 = +2.4%). */
export type AdminBotRow = {
  id: number;
  display_name: string;
  voting_power: number | null;
  accuracy: number | null;
  last_vote_at: number | null;
  last_picks: string[];
};
export type AdminBots = {
  bots: AdminBotRow[];
  llm: { reachable: boolean; detail?: string | null };
};

/** One executions row (+ display_name where the query joins members). status
 *  is kept open — deferrals ride inside `error` on skipped rows ("deferred —
 *  beyond settled buying power") and new statuses must render, not fail to
 *  compile. */
export type AdminExecRow = {
  member_id?: number;
  display_name?: string;
  symbol: string;
  side?: string | null;
  dollar_amount?: number | null;
  status: string;
  error?: string | null;
};

/** GET /api/admin/overview — the session + executions payload (the same one
 *  the forge dashboard consumed via its proxy). Raw SQL rows, so SQLite 0/1
 *  ints stand in for booleans. */
export type AdminOverviewMember = {
  id: number;
  email: string | null;
  display_name: string;
  role: string;
  is_bot?: number | null;
  bot_strategy?: string | null;
  bot_model?: string | null;
  opted_in?: number | null;
  voting_power?: number | null;
  account_number?: string | null;
  agentic_allowed?: number | null;
  rh_status?: string | null;
  power_delta?: number | null;
};
export type AdminOverview = {
  armed: boolean;
  halt: boolean;
  execution_mode: string;
  top_n: number;
  total_voting_power: number;
  bot_voting_power: number;
  invite_code?: string | null;
  owner_email?: string | null;
  schedule?: { open?: string; close?: string; execute?: string; weekly_power?: string; cadence?: string };
  members: AdminOverviewMember[];
  session: SessionView | null;
  executions: AdminExecRow[];
};

/** GET /api/admin/member/{id} — everything about one member. The live-broker
 *  extras (portfolio/holdings) are best-effort: absent for bots and for any
 *  member whose Robinhood read fails (rh_error says why). */
export type AdminMemberDetail = {
  member: {
    id: number; email: string | null; display_name: string; role: string;
    is_bot?: number | null; bot_strategy?: string | null; bot_model?: string | null;
    settings?: {
      opted_in?: number | null; voting_power?: number | null;
      alloc_type?: string | null; alloc_value?: number | null;
    } | null;
    robinhood?: { status?: string | null; account_number?: string | null } | null;
  };
  power?: PowerView | null;
  session_date?: string | null;
  picks?: { symbol: string; weight: number }[];
  bot_picks?: { symbol: string; weight_pct: number; entry_price?: number | null; thesis?: string | null }[];
  bot_thesis?: string | null;
  executions?: AdminExecRow[];
  portfolio?: {
    total_value: number | null; equity_value?: number | null;
    cash?: number | null; buying_power: number | null;
  } | null;
  holdings?: {
    symbol: string; quantity: number; avg_buy_price: number;
    last: number | null; market_value: number; pnl: number | null;
  }[];
  rh_error?: string | null;
};

/** POST /api/admin/run/{phase} — the result is phase-shaped, so typed loose;
 *  the console summarizes whatever orders/skips it finds. */
export type AdminRunResult = {
  mode?: string;
  note?: string;
  orders?: { symbol?: string; side?: string; status?: string; note?: string }[];
  placed_total?: number;
  skipped?: Record<string, number>;
  [k: string]: unknown;
};

export const api = {
  base: BASE,
  // auth — invite code + email + a password you create; log in with email + password.
  // consent = the club-agreement checkbox (required for new signups in prod).
  // signupStart: pre-flight that validates the invite and (if email verification is on)
  // sends a 6-digit code — signup then needs that code in email_code.
  signupStart: (invite_code: string, email: string) =>
    req<{ code_required: boolean }>("/api/auth/signup/start", {
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
  // server-side logout — clears the 30-day httpOnly session cookie, which the
  // client-only logout can't touch. credentials:'include' so the cookie
  // actually rides along (and gets deleted) across the Vercel↔API origin.
  logout: () =>
    req<{ ok: boolean }>("/api/auth/logout", { method: "POST", credentials: "include" }),
  // log out EVERYWHERE — session_version bump revokes every session on every
  // device (this one included); the caller must hard-logout locally after.
  // NO UI CALLS THIS TODAY (the "Log out everywhere" button was removed at the
  // owner's request); the endpoint and its tests stay, ready for a future one.
  logoutAll: () =>
    req<{ ok: boolean }>("/api/me/logout-all", { method: "POST", credentials: "include" }),
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
  // change the account email — start mails a 6-digit code to the NEW address
  // (never in the response); confirm swaps members.email and returns the fresh
  // member. Both session-only (403 on api-key auth).
  emailChangeStart: (new_email: string) =>
    req<{ sent: boolean }>("/api/me/email/start", {
      method: "POST", body: JSON.stringify({ new_email }),
    }),
  emailChangeConfirm: (new_email: string, code: string) =>
    req<{ member: Member }>("/api/me/email/confirm", {
      method: "POST", body: JSON.stringify({ new_email, code }),
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

  // agentic balance + allocation (% or $) + club-only P&L.
  // CHEAP: served from the backend's cached snapshot (refreshed on a ~10-min
  // timer), so this is safe to call on navigation. Live Robinhood reads happen
  // only on accountRefresh() below. Nothing should call either of these
  // directly any more — lib/auth.tsx owns the snapshot; read it from useAuth().
  account: () => req<Account>("/api/me/account"),
  // FORCED live pull — the member pressed "Refresh" on a stale snapshot. Costs
  // real broker round-trips and may 429 when spammed; never put it on a timer.
  refreshAccount: () => req<Account>("/api/me/account/refresh", { method: "POST" }),
  // is the RH account cleared to place buys (investing-goals questionnaire done)?
  // NOT part of the account snapshot — it has its own server-side TTL.
  tradeReadiness: (force = false) =>
    req<{ connected: boolean; ready: boolean; reason: string; detail?: string }>(
      `/api/me/trade-readiness${force ? "?force=true" : ""}`),
  // the member's agentic-account type + limited-margin upgrade links. A cash
  // account waits T+1 for sale proceeds (delayed redeploy); limited margin
  // trades on unsettled funds with no borrowing or leverage.
  marginStatus: (force = false) =>
    req<{
      connected: boolean; account_type?: string | null; delayed?: boolean | null;
      unsettled_funds?: string | null; acknowledged?: boolean;
      upgrade?: { web_upgrade_url: string; mobile_upgrade_url: string; upgrade_fallback: string } | null;
    }>(`/api/me/margin-status${force ? "?force=true" : ""}`),
  marginAck: (acknowledged: boolean) =>
    req<{ ok: boolean; acknowledged: boolean }>("/api/me/margin-ack", {
      method: "PUT", body: JSON.stringify({ acknowledged }),
    }),
  // the member's positions (for the overview donut + holdings list) — same
  // cached-snapshot contract as account().
  holdings: () => req<HoldingsView>("/api/me/holdings"),
  // The member's own RECORDED account-value curve for one window. Cheap: it
  // reads rows the 10-minute account refresher already wrote, and costs the
  // broker nothing. Callers must treat a 404 / a malformed body as "no history
  // yet" rather than an error — this endpoint post-dates some deployments, and
  // an account that started recording this morning genuinely has ~no rows.
  myEquity: (range: EquityRangeKey) =>
    req<EquityHistory>(`/api/me/equity?range=${encodeURIComponent(range)}`),
  setAllocation: (type: "percent" | "dollar", value: number) =>
    req<Account>("/api/me/allocation", { method: "PUT", body: JSON.stringify({ type, value }) }),
  // club decisions vs SPY, both indexed to 100 — club-level, identical for every member
  clubPerformance: () => req<PerfView>("/api/club/performance"),
  // history split — the fund's cycle-by-cycle record (club-shared, no member
  // identities) vs. the caller's own ballots (caller-only)
  clubHistory: (limit = 30) => req<ClubHistoryView>(`/api/club/history?limit=${limit}`),
  myVotes: (limit = 30) => req<MyVotesView>(`/api/me/votes?limit=${limit}`),

  // change how every other member sees you — session login only (403 on api-key)
  renameDisplayName: (display_name: string) =>
    req<{ member: Member }>("/api/me/display-name", {
      method: "PUT", body: JSON.stringify({ display_name }),
    }),

  // stocks — the search page + the per-symbol detail slide-over.
  // Detail is cheap to call: the backend caches per symbol (5-min quotes,
  // 1-hour history) and 404s cleanly on garbage.
  stockSearch: (q: string, limit = 20) =>
    req<{ results: StockSearchResult[] }>(
      `/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  stockDetail: (symbol: string) =>
    req<StockDetail>(`/api/stocks/${encodeURIComponent(symbol.trim().toUpperCase())}`),
  // One range's real bars, fetched only when that tab is opened — 1D/1W are
  // genuine intraday series, so they are NOT shipped with the detail payload.
  stockHistory: (symbol: string, range: StockRangeKey) =>
    req<StockHistory>(
      `/api/stocks/${encodeURIComponent(symbol.trim().toUpperCase())}/history?range=${range}`),

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
  // admin console (admin.voteconcordia.com) — the ops surface. Session-auth,
  // owner/admin only; plain members get a 403 whose detail ("admin access
  // required") surfaces through the normal ApiError path. The console is now
  // the primary ops surface (it replaced the forge-dashboard concordia page),
  // so the owner/admin mutations live here too — every one is audited
  // server-side, and the dangerous ones sit behind their own confirm
  // affordances in components/admin/StatusBar.tsx.
  adminPanel: () => req<AdminPanel>("/api/admin/panel"),
  adminOverview: () => req<AdminOverview>("/api/admin/overview"),
  adminSchedule: () => req<AdminSchedule>("/api/admin/schedule"),
  adminBots: () => req<AdminBots>("/api/admin/bots"),
  adminMember: (id: number) => req<AdminMemberDetail>(`/api/admin/member/${id}`),
  // arming lets real orders happen — the typed-ARM confirm lives in the UI
  adminArm: (armed: boolean) =>
    req<{ armed: boolean }>("/api/admin/arm", { method: "POST", body: JSON.stringify({ armed }) }),
  // halt + disarm in one shot — deliberately one click: stopping stays easy
  adminKill: () => req<{ armed: boolean; halt: boolean }>("/api/admin/kill", { method: "POST" }),
  // manual phase triggers — `execute` honours dry_run unless live+armed
  adminRun: (phase: "open" | "close" | "execute" | "power" | "bots" | "reweight", dryRun = true) =>
    req<AdminRunResult>(`/api/admin/run/${phase}?dry_run=${dryRun}`, { method: "POST" }),
  // admin-mediated password recovery — the temp password is returned ONCE
  adminResetPassword: (id: number) =>
    req<{ ok: boolean; email: string; temp_password: string }>(
      `/api/admin/member/${id}/reset-password`, { method: "POST" }),
  // morning digest on demand — same builder the 08:00 ET timer uses
  // (adminview.send_digest); printed=true means email is disabled and the
  // body went to the server log instead.
  adminSendDigest: () =>
    req<{ subject: string; printed: boolean; sent: string[]; failed: string[] }>(
      "/api/admin/digest", { method: "POST" }),
};
