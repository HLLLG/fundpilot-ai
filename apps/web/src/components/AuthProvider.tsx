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
import { ApiError, fetchCurrentUser } from "@/lib/api";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  bootstrapError: string | null;
  setSession: (accessToken: string, user: AuthUser) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_PATHS = new Set(["/login", "/register"]);
const AUTH_BOOTSTRAP_RETRIES = 5;
const AUTH_BOOTSTRAP_RETRY_MS = 800;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const pathname = usePathname();
  const router = useRouter();

  const refreshUser = useCallback(async () => {
    const token = getAccessToken();
    if (!token) {
      setUser(null);
      setBootstrapError(null);
      return;
    }

    for (let attempt = 0; attempt < AUTH_BOOTSTRAP_RETRIES; attempt += 1) {
      try {
        const me = await fetchCurrentUser();
        setUser(me);
        setBootstrapError(null);
        return;
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        if (status === 401) {
          clearAccessToken();
          setUser(null);
          setBootstrapError(null);
          return;
        }

        if (attempt < AUTH_BOOTSTRAP_RETRIES - 1) {
          await sleep(AUTH_BOOTSTRAP_RETRY_MS);
          continue;
        }

        setBootstrapError("无法连接服务器，请确认 API 已启动后重试");
      }
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
    const hasToken = Boolean(getAccessToken());

    if (!user && !isPublic && !hasToken) {
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
    setBootstrapError(null);
  }, []);

  const logout = useCallback(() => {
    clearAccessToken();
    setUser(null);
    setBootstrapError(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, bootstrapError, setSession, logout, refreshUser }),
    [user, loading, bootstrapError, setSession, logout, refreshUser],
  );

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        加载中…
      </div>
    );
  }

  if (!user && getAccessToken() && bootstrapError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50 px-4 text-center">
        <p className="text-sm text-slate-600">{bootstrapError}</p>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            void refreshUser().finally(() => setLoading(false));
          }}
          className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          重试连接
        </button>
      </div>
    );
  }

  if (!user && !PUBLIC_PATHS.has(pathname) && !getAccessToken()) {
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
