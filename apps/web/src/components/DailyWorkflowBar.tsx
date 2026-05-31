"use client";

import { ArrowRight, Sparkles } from "lucide-react";

type DailyWorkflowBarProps = {
  step: 1 | 2 | 3;
  holdingsCount: number;
  isParsing: boolean;
  isAnalyzing: boolean;
  canAnalyze: boolean;
  onRunDaily: () => void;
};

const steps = [
  { id: 1, label: "上传总览截图" },
  { id: 2, label: "校对持仓" },
  { id: 3, label: "生成日报" },
] as const;

export function DailyWorkflowBar({
  step,
  holdingsCount,
  isParsing,
  isAnalyzing,
  canAnalyze,
  onRunDaily,
}: DailyWorkflowBarProps) {
  const busy = isParsing || isAnalyzing;

  return (
    <section className="mb-5 rounded-[28px] border border-blue-200 bg-gradient-to-r from-blue-600 to-indigo-600 p-5 text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="mb-2 inline-flex items-center gap-2 text-xs font-bold text-blue-100">
            <Sparkles size={14} />
            今日工作流
          </div>
          <h2 className="text-lg font-black">一张总览图 → 校对 → 日报</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {steps.map((item) => {
              const active = step >= item.id;
              return (
                <span
                  key={item.id}
                  className={`rounded-full px-3 py-1 text-xs font-bold ${
                    active ? "bg-white text-blue-700" : "bg-white/15 text-blue-100"
                  }`}
                >
                  {item.id}. {item.label}
                </span>
              );
            })}
          </div>
        </div>
        <button
          type="button"
          disabled={!canAnalyze || busy}
          onClick={onRunDaily}
          className="inline-flex items-center justify-center gap-2 rounded-full bg-white px-5 py-3 text-sm font-black text-blue-700 shadow-lg transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:bg-white/50 disabled:text-blue-300"
        >
          {isParsing ? "正在识别截图..." : isAnalyzing ? "正在生成日报..." : "今日一键分析"}
          <ArrowRight size={16} />
        </button>
      </div>
      <p className="mt-3 text-xs text-blue-100">
        {holdingsCount > 0
          ? `已识别 ${holdingsCount} 条持仓，请快速校对后一键生成。`
          : "上传养基宝总览截图后，将自动识别并进入分析。"}
      </p>
    </section>
  );
}
