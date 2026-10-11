import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setActor } from "./actor";
import type { RoleId } from "./role";

const BASE = (import.meta.env.VITE_API as string | undefined) ?? `${import.meta.env.BASE_URL}api`;

export type AuthUser = { id: number; username: string; name: string; email: string | null; picture: string | null; role: RoleId; home_role: RoleId; master: boolean };
export type AuthConfig = {
  enabled: boolean;
  google_client_id: string | null;
  roles: { id: RoleId; label: string; blurb: string }[];
  demo_accounts: { username: string; role: RoleId; label: string }[];
  demo_password: string | null;
};
type Result = { ok: true; needsRole?: { email: string; name: string } } | { ok: false; error: string };
type Ctx = {
  /** off: this server has no sign-in (local runs). */
  status: "loading" | "off" | "anon" | "user";
  user: AuthUser | null;
  config: AuthConfig | null;
  login: (username: string, password: string) => Promise<Result>;
  signup: (v: { username: string; password: string; name: string; role: RoleId }) => Promise<Result>;
  google: (credential: string, role?: RoleId) => Promise<Result>;
  logout: () => Promise<void>;
  switchRole: (role: RoleId) => Promise<void>;
};
const AuthContext = createContext<Ctx | null>(null);

async function call(path: string, body?: unknown): Promise<{ status: number; data: any }> {
  const r = await fetch(BASE + path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data: unknown = null;
  try {
    data = await r.json();
  } catch {
    /* not JSON */
  }
  return { status: r.status, data };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Ctx["status"]>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await call("/auth/config");
        if (cfg.status !== 200 || !cfg.data?.enabled) return setStatus("off");
        setConfig(cfg.data as AuthConfig);
        const me = await call("/auth/me");
        setUser(me.data?.user ?? null);
        setStatus(me.data?.user ? "user" : "anon");
      } catch {
        setStatus("off"); // no API answer: do not lock the screen; the pages will say the API is down
      }
    })();
    const lost = () => setStatus((s) => (s === "user" ? "anon" : s));
    window.addEventListener("aerotwin:auth-lost", lost);
    return () => window.removeEventListener("aerotwin:auth-lost", lost);
  }, []);

  useEffect(() => {
    setActor(user ? user.name : "");
  }, [user]);

  const enter = (data: any): Result => {
    if (data?.user) {
      setUser(data.user as AuthUser);
      setStatus("user");
      return { ok: true };
    }
    if (data?.needs_role) return { ok: true, needsRole: { email: data.email, name: data.name } };
    return { ok: false, error: "Sign-in failed." };
  };
  const wrap = async (path: string, body: unknown): Promise<Result> => {
    try {
      const r = await call(path, body);
      return r.status === 200 ? enter(r.data) : { ok: false, error: r.data?.detail ?? "Sign-in failed." };
    } catch {
      return { ok: false, error: "Could not reach the server." };
    }
  };
  const login = useCallback((username: string, password: string) => wrap("/auth/login", { username, password }), []);
  const signup = useCallback((v: { username: string; password: string; name: string; role: RoleId }) => wrap("/auth/signup", v), []);
  const google = useCallback((credential: string, role?: RoleId) => wrap("/auth/google", { credential, role }), []);
  const logout = useCallback(async () => {
    await call("/auth/logout", {});
    setUser(null);
    setStatus("anon");
  }, []);
  const switchRole = useCallback(async (role: RoleId) => {
    const r = await call("/auth/role", { role });
    if (r.status === 200 && r.data?.user) setUser(r.data.user as AuthUser);
  }, []);

  const value = useMemo(() => ({ status, user, config, login, signup, google, logout, switchRole }), [status, user, config, login, signup, google, logout, switchRole]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Ctx {
  const c = useContext(AuthContext);
  if (!c) throw new Error("useAuth must be used inside AuthProvider");
  return c;
}
