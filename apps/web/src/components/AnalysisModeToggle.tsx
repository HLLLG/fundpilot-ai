"use client";

import type { AnalysisMode } from "@/lib/api";

type AnalysisModeToggleProps = {
  mode: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
  compact?: boolean;
};

export function AnalysisModeToggle({ mode, onChange, compact = false }: AnalysisModeToggleProps) {
  if (compact) {
    return (
      <div className="tab-segment">
        <button
          type="button"
          onClick={() => onChange("fast")}
          aria-pressed={mode === "fast"}
          className="tab-segment-btn"
        >
          快速
        </button>
        <button
          type="button"
          onClick={() => onChange("deep")}
          aria-pressed={mode === "deep"}
          className="tab-segment-btn"
        >
          深度
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-3xl bg-white p-2 shadow-sm">
      <div className="mb-2 px-2 text-xs font-bold text-slate-400">分析模式</div>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => onChange("fast")}
          className={`rounded-2xl px-3 py-2.5 text-sm font-bold transition ${
            mode === "fast"
              ? "bg-amber-500 text-white shadow-md"
              : "bg-slate-50 text-slate-600 hover:bg-amber-50"
          }`}
        >
          快速
        </button>
        <button
          type="button"
          onClick={() => onChange("deep")}
          className={`rounded-2xl px-3 py-2.5 text-sm font-bold transition ${
            mode === "deep"
              ? "bg-[var(--brand)] text-white shadow-md"
              : "bg-slate-50 text-slate-600 hover:bg-[var(--brand-soft)]"
          }`}
        >
          深度
        </button>
      </div>
    </div>
  );
}
