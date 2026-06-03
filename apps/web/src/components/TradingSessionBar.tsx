"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Clock3 } from "lucide-react";
import { fetchTradingSession, type TradingSession } from "@/lib/api";

const sessionTone: Record<string, string> = {
  trading_day_pre_close: "border-amber-200 bg-amber-50 text-amber-950",
  trading_day_after_close: "border-slate-200 bg-slate-50 text-slate-800",
  trading_day_intraday: "border-blue-200 bg-blue-50 text-blue-950",
  non_trading_day: "border-violet-200 bg-violet-50 text-violet-950",
};

const sessionLabel: Record<string, string> = {
  trading_day_pre_close: "收盘前决策窗口",
  trading_day_after_close: "已收盘",
  trading_day_intraday: "盘中",
  non_trading_day: "非交易日",
};

type LoadState = "loading" | "ready" | "error";

export function TradingSessionBar() {
  const [session, setSession] = useState<TradingSession | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  useEffect(() => {
    void fetchTradingSession()
      .then((payload) => {
        setSession(payload);
        setLoadState("ready");
      })
      .catch(() => {
        setSession(null);
        setLoadState("error");
      });
  }, []);

  if (loadState === "loading") {
    return (
      <div className="animate-pulse rounded-2xl border border-slate-200 bg-white/70 px-4 py-3">
        <div className="h-4 w-40 rounded bg-slate-200" />
        <div className="mt-2 h-3 w-64 rounded bg-slate-100" />
      </div>
    );
  }

  if (loadState === "error" || !session) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-slate-600">
        <div className="flex items-center gap-2 text-sm font-bold">
          <AlertCircle size={16} className="text-slate-400" />
          <span>交易日历暂不可用</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          不影响持仓刷新与日报生成，请确认后端 API 已启动。
        </p>
      </div>
    );
  }

  const tone = sessionTone[session.session_kind] ?? sessionTone.trading_day_intraday;
  const label = sessionLabel[session.session_kind] ?? "交易日";

  return (
    <div className={`rounded-2xl border px-4 py-3 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm font-bold">
        <Clock3 size={16} />
        <span>{label}</span>
        <span className="font-normal opacity-80">· {session.local_datetime}</span>
        {session.minutes_to_close != null && session.minutes_to_close >= 0 ? (
          <span className="rounded-full bg-white/70 px-2 py-0.5 text-xs font-semibold">
            距收盘约 {session.minutes_to_close} 分钟
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-xs leading-5 opacity-90">{session.decision_window}</p>
    </div>
  );
}
