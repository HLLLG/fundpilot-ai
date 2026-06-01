"use client";

import { Gauge, Zap } from "lucide-react";
import type { AnalysisMode } from "@/lib/api";

type AnalysisModeToggleProps = {
  mode: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
};

export function AnalysisModeToggle({ mode, onChange }: AnalysisModeToggleProps) {
  return (
    <div className="rounded-3xl bg-white p-2 shadow-sm">
      <div className="mb-2 px-2 text-xs font-bold text-slate-400">分析模式</div>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => onChange("fast")}
          className={`flex items-center gap-2 rounded-2xl px-3 py-2.5 text-left text-sm font-bold transition ${
            mode === "fast"
              ? "bg-amber-500 text-white shadow-md"
              : "bg-slate-50 text-slate-600 hover:bg-amber-50"
          }`}
        >
          <Zap size={16} />
          <span>
            快速
            <span className={`mt-0.5 block text-[10px] font-semibold ${mode === "fast" ? "text-amber-100" : "text-slate-400"}`}>
              Flash · 预取+主题摘要
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={() => onChange("deep")}
          className={`flex items-center gap-2 rounded-2xl px-3 py-2.5 text-left text-sm font-bold transition ${
            mode === "deep"
              ? "bg-blue-600 text-white shadow-md"
              : "bg-slate-50 text-slate-600 hover:bg-blue-50"
          }`}
        >
          <Gauge size={16} />
          <span>
            深度
            <span className={`mt-0.5 block text-[10px] font-semibold ${mode === "deep" ? "text-blue-100" : "text-slate-400"}`}>
              Pro · 摘要+新闻 Tool
            </span>
          </span>
        </button>
      </div>
    </div>
  );
}
