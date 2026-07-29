"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, getToken, setToken } from "@/lib/api";
import type { Account, Holding, Member, PowerView, SessionView } from "@/lib/types";

type Ctx = {
  ready: boolean;
  member: Member | null;
  power: PowerView | null;
  session: SessionView | null;
  /** Backend unreachable (network error or 5xx). Session is kept; we retry in the background. */
  offline: boolean;
  /** Last error from the ballot fetch — lets the vote page show an error instead of spinning forever. */
  sessionError: string | null;

  // ── the account snapshot ────────────────────────────────────────────────
  // Fetched ONCE when the member is established (login or session restore),
  // then kept warm on a 5-minute cheap poll. Every screen reads it from here;
  // nothing calls /api/me/account or /api/me/holdings on navigation any more.
  account: Account | null;
  holdings: Holding[];
  /** epoch SECONDS of the snapshot the numbers came from; null = unknown. */
  accountAsOf: number | null;
  /** the backend says this snapshot is older than 30 minutes. */
  accountStale: boolean;
  /** the first account attempt has SETTLED (success or failure). Never blocks on an error. */
  accountReady: boolean;
  /** last account error, kept alongside the last good numbers rather than replacing them. */
  accountError: string | null;
  /** a forced refresh is in flight (for the manual "Refresh" affordance). */
  accountRefreshing: boolean;
  /** forced live broker pull — user-initiated only (POST, may 429). */
  refreshAccount: () => Promise<void>;
  /** cheap cached re-read — safe any time. */
  reloadAccount: () => Promise<void>;
  /** push a fresh payload straight in (e.g. what PUT /me/allocation returns) — no refetch. */
  applyAccount: (a: Account) => void;

  setMember: (m: Member | null) => void;
  refreshMember: () => Promise<void>;
  refreshSession: () => Promise<void>;
  logout: () => void;
};

const AuthCtx = createContext<Ctx | null>(null);
export function useAuth(): Ctx {
  const c = useContext(AuthCtx);
  if (!c) throw new Error("useAuth must be used within AuthProvider");
  return c;
}

// Only 401/403 means the token is actually bad. Everything else (network drop, 5xx)
// is a blip — never log a member out over one.
function isAuthError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}
// Network failure (no .status at all) or a 5xx = the backend is unreachable/unwell.
function isBlip(e: unknown): boolean {
  return !(e instanceof ApiError) || e.status >= 500;
}
function msgOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Keep the poll off the wire while the tab is buried. */
const POLL_MS = 5 * 60_000;
/** A visibility flip inside this window reuses what we already have — coming
 *  back from a 3-second tab switch shouldn't cost a round-trip; coming back
 *  from overnight should. */
const VIS_MIN_AGE_MS = 60_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [member, setMember] = useState<Member | null>(null);
  const [power, setPower] = useState<PowerView | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [offline, setOffline] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const [account, setAccount] = useState<Account | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [accountReady, setAccountReady] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [accountRefreshing, setAccountRefreshing] = useState(false);
  const lastFetchRef = useRef(0);      // ms epoch of the last attempt (any outcome)
  const inFlightRef = useRef(false);   // one account fetch at a time

  const refreshMember = useCallback(async () => {
    try {
      const res = await api.me();
      setMember(res.member);
      setPower(res.power);
      setOffline(false);
    } catch (e) {
      if (isAuthError(e)) {
        setMember(null);
        setPower(null);
        setToken(null);
        setOffline(false);
      } else if (isBlip(e)) {
        setOffline(true); // keep the session — backend blip, not a logout
      }
    }
  }, []);

  const refreshSession = useCallback(async () => {
    try {
      setSession(await api.voteToday());
      setSessionError(null);
      setOffline(false);
    } catch (e) {
      if (isAuthError(e)) {
        setMember(null);
        setPower(null);
        setToken(null);
      } else {
        setSessionError(e instanceof Error ? e.message : "Can't reach Concordia right now");
        if (isBlip(e)) setOffline(true);
      }
    }
  }, []);

  // The one place account money is fetched. `force` swaps the cheap cached GET
  // for the live POST. Failure NEVER blocks the app and NEVER logs anyone out:
  // we keep the last good numbers, surface the error, and flip accountReady so
  // the shell renders — a broker blip must not lock a member out of voting.
  const loadAccount = useCallback(async (force = false) => {
    if (inFlightRef.current && !force) return;
    inFlightRef.current = true;
    lastFetchRef.current = Date.now();
    if (force) setAccountRefreshing(true);
    try {
      const [a, h] = await Promise.allSettled([
        force ? api.refreshAccount() : api.account(),
        api.holdings(),
      ]);
      if (a.status === "fulfilled") {
        setAccount(a.value);
        setAccountError(null);
      } else {
        setAccountError(msgOf(a.reason, "couldn't load your account right now"));
        if (isBlip(a.reason)) setOffline(true);
      }
      // Holdings can legitimately be empty; only overwrite on success so a blip
      // doesn't wipe the donut.
      if (h.status === "fulfilled") setHoldings(h.value?.holdings || []);
    } finally {
      inFlightRef.current = false;
      if (force) setAccountRefreshing(false);
      setAccountReady(true);
    }
  }, []);

  const refreshAccount = useCallback(() => loadAccount(true), [loadAccount]);
  const reloadAccount = useCallback(() => loadAccount(false), [loadAccount]);
  const applyAccount = useCallback((a: Account) => {
    setAccount(a);
    setAccountError(null);
    lastFetchRef.current = Date.now();
  }, []);

  useEffect(() => {
    (async () => {
      if (getToken()) await refreshMember();
      setReady(true);
    })();
  }, [refreshMember]);

  useEffect(() => {
    if (!member) { setSession(null); setSessionError(null); return; }
    refreshSession();
    const id = setInterval(refreshSession, 6000);
    return () => clearInterval(id);
  }, [member, refreshSession]);

  // ── the once-per-member account fetch ──────────────────────────────────
  // Keyed on identity + brokerage link, NOT on the member object: refreshMember()
  // hands back a new object on every mutation and we must not re-pay the fetch
  // for those. Connecting (or losing) Robinhood DOES change the key, because
  // that's exactly when the snapshot becomes meaningful — which is what makes
  // the /welcome wizard pick up a balance the moment StepConnect finishes.
  const rh = member?.robinhood;
  const accountKey = member ? `${member.id}|${rh?.status ?? ""}|${rh?.account_number ?? ""}` : null;

  useEffect(() => {
    if (!accountKey) {
      // no member (signed out, or a bad token dropped us) — nothing to show
      setAccount(null); setHoldings([]); setAccountReady(false); setAccountError(null);
      return;
    }
    loadAccount(false);
  }, [accountKey, loadAccount]);

  // Keep it warm while the tab is open: cheap cached reads only, never the
  // forced live pull. Skipped entirely when the tab is hidden.
  useEffect(() => {
    if (!accountKey) return;
    const id = setInterval(() => { if (!document.hidden) loadAccount(false); }, POLL_MS);
    const onVis = () => {
      // Back from a tab left open overnight → re-read immediately rather than
      // showing yesterday's number until the next tick.
      if (!document.hidden && Date.now() - lastFetchRef.current > VIS_MIN_AGE_MS) loadAccount(false);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", onVis); };
  }, [accountKey, loadAccount]);

  // While offline, keep knocking every ~15s — the first success clears the flag.
  useEffect(() => {
    if (!offline) return;
    const id = setInterval(() => { if (getToken()) refreshMember(); }, 15000);
    return () => clearInterval(id);
  }, [offline, refreshMember]);

  const logout = useCallback(() => {
    setToken(null); setMember(null); setPower(null); setSession(null);
    setOffline(false); setSessionError(null);
    setAccount(null); setHoldings([]); setAccountReady(false);
    setAccountError(null); setAccountRefreshing(false);
    lastFetchRef.current = 0;
  }, []);

  return (
    <AuthCtx.Provider value={{
      ready, member, power, session, offline, sessionError,
      account, holdings,
      accountAsOf: account?.as_of ?? null,
      accountStale: account?.stale === true,
      accountReady, accountError, accountRefreshing,
      refreshAccount, reloadAccount, applyAccount,
      setMember, refreshMember, refreshSession, logout,
    }}>
      {children}
    </AuthCtx.Provider>
  );
}
