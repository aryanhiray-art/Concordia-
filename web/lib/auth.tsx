"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";
import type { Member, PowerView, SessionView } from "@/lib/types";

type Ctx = {
  ready: boolean;
  member: Member | null;
  power: PowerView | null;
  session: SessionView | null;
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [member, setMember] = useState<Member | null>(null);
  const [power, setPower] = useState<PowerView | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);

  const refreshMember = useCallback(async () => {
    try {
      const res = await api.me();
      setMember(res.member);
      setPower(res.power);
    } catch {
      setMember(null);
      setPower(null);
      setToken(null);
    }
  }, []);

  const refreshSession = useCallback(async () => {
    try { setSession(await api.voteToday()); } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    (async () => {
      if (getToken()) await refreshMember();
      setReady(true);
    })();
  }, [refreshMember]);

  useEffect(() => {
    if (!member) { setSession(null); return; }
    refreshSession();
    const id = setInterval(refreshSession, 6000);
    return () => clearInterval(id);
  }, [member, refreshSession]);

  const logout = useCallback(() => { setToken(null); setMember(null); setPower(null); setSession(null); }, []);

  return (
    <AuthCtx.Provider value={{ ready, member, power, session, setMember, refreshMember, refreshSession, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}
