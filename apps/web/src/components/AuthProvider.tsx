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
import { BrandMark } from "@/components/BrandMark";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  bootstrapError: string | null;
  setSession: (accessToken: string, user: AuthUser) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_PATHS = new Set(["/", "/login", "/register"]);
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
    const isLanding = pathname === "/";
    const hasToken = Boolean(getAccessToken());

    if (!user && !isPublic && !isLanding && !hasToken) {
      const redirect = encodeURIComponent(pathname);
      router.replace(`/login?redirect=${redirect}`);
      return;
    }
    // 已登录用户在根路径本身就会渲染 Dashboard；不要再次 replace("/")，否则会
    // 清掉日报导航使用的 ?report=... 可恢复状态。登录/注册页仍回到工作台。
    if (user && isPublic && pathname !== "/") {
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

  const retryBootstrap = () => {
    setLoading(true);
    void refreshUser().finally(() => setLoading(false));
  };

  const clearBrokenSession = () => {
    clearAccessToken();
    setUser(null);
    setBootstrapError(null);
    router.replace("/");
  };

  if (loading && !PUBLIC_PATHS.has(pathname)) {
    return (
      <main className="premium-bg flex min-h-screen items-center justify-center px-4">
        <div className="section-card flex items-center gap-3 px-5 py-4 text-sm text-slate-600" role="status">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--brand)]" aria-hidden />
          正在恢复登录状态…
        </div>
      </main>
    );
  }

  if (!user && getAccessToken() && bootstrapError) {
    if (PUBLIC_PATHS.has(pathname)) {
      return (
        <AuthContext.Provider value={value}>
          <div
            className="fixed inset-x-3 top-3 z-[80] mx-auto flex max-w-2xl flex-col gap-2 rounded-2xl border border-amber-200 bg-amber-50/95 px-4 py-3 text-sm text-amber-950 shadow-lg backdrop-blur sm:flex-row sm:items-center sm:justify-between"
            role="alert"
          >
            <span className="leading-6">登录状态暂时无法验证，公开页面仍可正常浏览。</span>
            <div className="flex shrink-0 gap-2">
              <button type="button" onClick={retryBootstrap} className="min-h-11 rounded-full bg-amber-950 px-4 text-xs font-bold text-white">
                重试连接
              </button>
              <button type="button" onClick={clearBrokenSession} className="min-h-11 rounded-full border border-amber-300 bg-white px-4 text-xs font-bold text-amber-950">
                清除失效登录
              </button>
            </div>
          </div>
          {children}
        </AuthContext.Provider>
      );
    }

    return (
      <main className="premium-bg flex min-h-screen items-center justify-center px-4 py-10">
        <section className="section-card w-full max-w-md p-6 text-center sm:p-8" aria-labelledby="auth-recovery-title">
          <div className="mb-6 flex justify-center"><BrandMark size="lg" showEnglish /></div>
          <h1 id="auth-recovery-title" className="text-xl font-black text-slate-950">暂时无法恢复登录</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600" role="alert">{bootstrapError}</p>
          <div className="mt-6 grid gap-2 sm:grid-cols-2">
            <button type="button" onClick={retryBootstrap} className="btn-primary w-full">重试连接</button>
            <button type="button" onClick={() => router.replace("/")} className="btn-secondary w-full">返回公开首页</button>
          </div>
          <button type="button" onClick={clearBrokenSession} className="btn-ghost mt-2 w-full text-amber-800">
            清除失效登录状态
          </button>
        </section>
      </main>
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
