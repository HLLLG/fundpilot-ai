"use client";

import { useEffect, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { fetchRebalanceSimulation, type RebalanceSimulation } from "@/lib/api";

type RebalanceSimulationPanelProps = {
  reportId: string;
  embedded?: boolean;
};

export function RebalanceSimulationPanel({
  reportId,
  embedded = false,
}: RebalanceSimulationPanelProps) {
  const [simulation, setSimulation] = useState<RebalanceSimulation | null>(null);

  useEffect(() => {
    void fetchRebalanceSimulation(reportId)
      .then(setSimulation)
      .catch(() => setSimulation(null));
  }, [reportId]);

  if (!simulation) {
    return null;
  }

  const inner = (
    <>
      <p className="mb-3 text-xs leading-5 text-slate-600">{simulation.assumption}</p>
      <div className="mb-3 flex flex-wrap gap-4 text-sm font-semibold text-slate-700">
        <span>当前总额 ¥{simulation.current_total.toLocaleString("zh-CN")}</span>
        <span>模拟后 ¥{simulation.simulated_total.toLocaleString("zh-CN")}</span>
      </div>
      {simulation.warnings.length > 0 ? (
        <ul className="mb-3 space-y-1 text-xs font-semibold text-amber-800">
          {simulation.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      <div className="overflow-x-auto rounded-2xl bg-white">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-3 py-2">基金</th>
              <th className="px-3 py-2">动作</th>
              <th className="px-3 py-2">变动</th>
              <th className="px-3 py-2">仓位%</th>
              <th className="px-3 py-2">模拟后%</th>
            </tr>
          </thead>
          <tbody>
            {simulation.rows.map((row) => (
              <tr key={row.fund_code} className="border-t border-slate-100 text-slate-700">
                <td className="px-3 py-2 font-semibold">{row.fund_name}</td>
                <td className="px-3 py-2">{row.action}</td>
                <td className="px-3 py-2">
                  {row.delta_yuan > 0 ? "+" : ""}
                  {row.delta_yuan.toLocaleString("zh-CN")}
                </td>
                <td className="px-3 py-2">{row.current_weight_percent}</td>
                <td className="px-3 py-2">
                  {row.simulated_weight_percent}
                  <span className="text-slate-400">
                    {" "}
                    ({row.weight_delta_percent > 0 ? "+" : ""}
                    {row.weight_delta_percent})
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );

  if (embedded) {
    return inner;
  }

  return (
    <div className="mb-5 rounded-[24px] border border-emerald-100 bg-emerald-50/50 p-5">
      <div className="mb-2 flex items-center gap-2 text-sm font-black text-slate-950">
        <SlidersHorizontal size={18} className="text-emerald-700" />
        模拟调仓（按报告示意金额）
      </div>
      {inner}
    </div>
  );
}
