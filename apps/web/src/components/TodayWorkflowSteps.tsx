"use client";

import { CheckCircle2, Circle } from "lucide-react";

type TodayWorkflowStepsProps = {
  hasHoldings: boolean;
  hasReport: boolean;
};

const steps = [
  { id: 1, label: "上传养基宝总览截图" },
  { id: 2, label: "校对持仓并确认风控" },
  { id: 3, label: "生成并查看今日日报" },
];

export function TodayWorkflowSteps({ hasHoldings, hasReport }: TodayWorkflowStepsProps) {
  const activeStep = hasReport ? 3 : hasHoldings ? 2 : 1;

  return (
    <div className="glass-panel grid gap-2 rounded-[24px] p-3 sm:grid-cols-3">
      {steps.map((step) => {
        const done = step.id < activeStep;
        const active = step.id === activeStep;
        return (
          <div
            key={step.id}
            className={`flex items-center gap-2 rounded-[18px] px-3 py-2.5 text-sm font-bold ${
              active
                ? "bg-blue-600 text-white"
                : done
                  ? "bg-emerald-50 text-emerald-800"
                  : "bg-white text-slate-500"
            }`}
          >
            {done ? (
              <CheckCircle2 size={18} className="shrink-0" />
            ) : (
              <Circle size={18} className={`shrink-0 ${active ? "text-white" : "text-slate-300"}`} />
            )}
            <span>
              <span className="mr-1 opacity-80">{step.id}.</span>
              {step.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
