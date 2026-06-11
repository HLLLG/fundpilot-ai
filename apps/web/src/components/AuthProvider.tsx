"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  clearAccessToken,
  getAccessToken,
  saveAccessToken,
  type AuthUser,
} from "@/lib/auth";
import { fetchCurrentUser } from "@/lib/api";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  setSession: (accessToken: string, user: AuthUser) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_PATHS = new Set(["/login", "/register"]);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  const refreshUser = useCallback(async () => {
    const token = getAccessToken();
    if (!token) {
      setUser(null);
      return;
    }
    try {
      const me = await fetchCurrentUser();
      setUser(me);
    } catch {
      clearAccessToken();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refreshUser();
      if (!cancelled) {
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshUser]);

  useEffect(() => {
    if (loading) {
      return;
    }
    const isPublic = PUBLIC_PATHS.has(pathname);
    if (!user && !isPublic) {
      const redirect = encodeURIComponent(pathname);
      router.replace(`/login?redirect=${redirect}`);
      return;
    }
    if (user && isPublic) {
      router.replace("/");
    }
  }, [loading, user, pathname, router]);

  const setSession = useCallback((accessToken: string, nextUser: AuthUser) => {
    saveAccessToken(accessToken);
    setUser(nextUser);
  }, []);

  const logout = useCallback(() => {
    clearAccessToken();
    setUser(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, setSession, logout, refreshUser }),
    [user, loading, setSession, logout, refreshUser],
  );

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        加载中…
      </div>
    );
  }

  if (!user && !PUBLIC_PATHS.has(pathname)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        跳转登录…
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
