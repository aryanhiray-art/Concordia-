"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, getToken, setToken } from "@/lib/api";
import type { Member, PowerView, SessionView } from "@/lib/types";

type Ctx = {
  ready: boolean;
  member: Member | null;
  power: PowerView | null;
  session: SessionView | null;
  /** Backend unreachable (network error or 5xx). Session is kept; we retry in the background. */
  offline: boolean;
  /** Last error from the ballot fetch — lets the vote page show an error instead of spinning forever. */
  sessionError: string | null;
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [member, setMember] = useState<Member | null>(null);
  const [power, setPower] = useState<PowerView | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [offline, setOffline] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);

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

  // While offline, keep knocking every ~15s — the first success clears the flag.
  useEffect(() => {
    if (!offline) return;
    const id = setInterval(() => { if (getToken()) refreshMember(); }, 15000);
    return () => clearInterval(id);
  }, [offline, refreshMember]);

  const logout = useCallback(() => {
    setToken(null); setMember(null); setPower(null); setSession(null);
    setOffline(false); setSessionError(null);
  }, []);

  return (
    <AuthCtx.Provider value={{ ready, member, power, session, offline, sessionError, setMember, refreshMember, refreshSession, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}
