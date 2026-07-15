"use client";

import type { AnalysisMode } from "@/lib/api";

type AnalysisModeToggleProps = {
  mode: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
  compact?: boolean;
};

const MODE_COPY: Record<
  AnalysisMode,
  { label: string; model: string; description: string }
> = {
  fast: {
    label: "快速",
    model: "Flash",
    description: "较少主题预取",
  },
  deep: {
    label: "深度",
    model: "Pro",
    description: "有界扩展证据 · 可选风控审校",
  },
};

export function AnalysisModeToggle({ mode, onChange, compact = false }: AnalysisModeToggleProps) {
  if (compact) {
    return (
      <div>
        <div className="tab-segment" role="group" aria-label="分析模式">
          {(Object.keys(MODE_COPY) as AnalysisMode[]).map((option) => {
            const copy = MODE_COPY[option];
            return (
              <button
                key={option}
                type="button"
                onClick={() => onChange(option)}
                aria-pressed={mode === option}
                className="tab-segment-btn"
              >
                {copy.label} · {copy.model}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] font-medium text-slate-500" aria-live="polite">
          {MODE_COPY[mode].model} 模型 · {MODE_COPY[mode].description}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-3xl bg-white p-2 shadow-sm">
      <div className="mb-2 px-2 text-xs font-bold text-slate-500">分析模式</div>
      <div className="grid grid-cols-2 gap-2">
        {(Object.keys(MODE_COPY) as AnalysisMode[]).map((option) => {
          const copy = MODE_COPY[option];
          return (
            <button
              key={option}
              type="button"
              onClick={() => onChange(option)}
              aria-pressed={mode === option}
              aria-label={`${copy.label} · ${copy.model}：${copy.description}`}
              className={`min-h-16 rounded-2xl px-3 py-2.5 text-left transition ${
                mode === option
                  ? option === "fast"
                    ? "bg-amber-700 text-white shadow-md"
                    : "bg-[var(--brand)] text-white shadow-md"
                  : option === "fast"
                    ? "bg-slate-50 text-slate-600 hover:bg-amber-50"
                    : "bg-slate-50 text-slate-600 hover:bg-[var(--brand-soft)]"
              }`}
            >
              <span className="block text-sm font-bold">
                {copy.label} · {copy.model}
              </span>
              <span className="mt-1 block text-[11px] font-medium opacity-80">
                {copy.description}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
