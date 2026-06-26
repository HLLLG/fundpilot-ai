"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Clock3 } from "lucide-react";
import { type TradingSession } from "@/lib/api";
import { readTradingSessionCache } from "@/lib/holdingDetailCache";
import { hydrateTradingSession } from "@/lib/tradingSessionClient";

const sessionTone: Record<string, string> = {
  trading_day_pre_open: "border-slate-200 bg-slate-50 text-slate-800",
  trading_day_pre_close: "border-amber-200 bg-amber-50 text-amber-950",
  trading_day_after_close: "border-slate-200 bg-slate-50 text-slate-800",
  trading_day_intraday: "border-blue-200 bg-blue-50 text-blue-950",
  non_trading_day: "border-slate-200 bg-slate-50 text-slate-800",
};

const sessionLabel: Record<string, string> = {
  trading_day_pre_open: "开盘前",
  trading_day_pre_close: "收盘前决策窗口",
  trading_day_after_close: "已收盘",
  trading_day_intraday: "盘中",
  non_trading_day: "非交易日",
};

type LoadState = "loading" | "ready" | "error";

export function TradingSessionBar() {
  const [session, setSession] = useState<TradingSession | null>(() => readTradingSessionCache());
  const [loadState, setLoadState] = useState<LoadState>(() =>
    readTradingSessionCache() ? "ready" : "loading",
  );

  useEffect(() => {
    return hydrateTradingSession(
      (payload) => {
        setSession(payload);
        setLoadState("ready");
      },
      () => {
        setSession(null);
        setLoadState("error");
      },
    );
  }, []);

  if (loadState === "loading") {
    return (
      <div className="animate-pulse section-card h-9 px-4 py-2">
        <div className="h-3 w-32 rounded bg-slate-200" />
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
    <div className={`section-card flex flex-wrap items-center gap-2 border px-3 py-2 text-xs font-semibold ${tone}`}>
      <Clock3 size={14} className="shrink-0" />
      <span>{label}</span>
      <span className="font-normal opacity-75">{session.effective_trade_date}</span>
      {session.minutes_to_close != null && session.minutes_to_close >= 0 ? (
        <span className="rounded-md bg-white/60 px-1.5 py-0.5 text-[11px]">
          距收盘 {session.minutes_to_close} 分
        </span>
      ) : null}
    </div>
  );
}
