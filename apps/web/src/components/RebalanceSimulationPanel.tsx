"use client";

import { useEffect, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { InlineNotice } from "@/components/InlineNotice";
import { fetchRebalanceSimulation, type RebalanceSimulation } from "@/lib/api";
import { useMediaQuery } from "@/lib/useMediaQuery";

const DESKTOP_QUERY = "(min-width: 640px)";

type RebalanceSimulationPanelProps = {
  reportId: string;
  embedded?: boolean;
};

export function RebalanceSimulationPanel({
  reportId,
  embedded = false,
}: RebalanceSimulationPanelProps) {
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const [result, setResult] = useState<{
    reportId: string;
    data: RebalanceSimulation;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorResult, setErrorResult] = useState<{
    reportId: string;
    message: string;
  } | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);
  const simulation = result?.reportId === reportId ? result.data : null;
  const error = errorResult?.reportId === reportId ? errorResult.message : null;
  const pending = loading || (!simulation && !error);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErrorResult(null);
    void fetchRebalanceSimulation(reportId)
      .then((data) => {
        if (!cancelled) {
          setResult({ reportId, data });
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setErrorResult({
            reportId,
            message: loadError instanceof Error ? loadError.message : "模拟调仓加载失败",
          });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reportId, retrySequence]);

  const status = error ? (
    <InlineNotice
      tone={simulation ? "warning" : "error"}
      message={
        simulation
          ? `模拟调仓更新失败，继续显示上次成功获取的结果：${error}`
          : `模拟调仓加载失败：${error}`
      }
      action={{ label: "重试", onClick: () => setRetrySequence((current) => current + 1) }}
    />
  ) : pending ? (
    <InlineNotice
      tone="info"
      message={simulation ? "正在更新模拟调仓，当前继续显示已有结果。" : "正在加载模拟调仓…"}
    />
  ) : null;

  const inner = (
    <div className="space-y-3" aria-busy={pending}>
      {status}
      {simulation ? (
        <>
          <p className="text-xs leading-5 text-slate-600">{simulation.assumption}</p>
          <div className="flex flex-wrap gap-4 text-sm font-semibold text-slate-700">
            <span>当前总额 ¥{simulation.current_total.toLocaleString("zh-CN")}</span>
            <span>模拟后 ¥{simulation.simulated_total.toLocaleString("zh-CN")}</span>
          </div>
          {simulation.warnings.length > 0 ? (
            <ul className="space-y-1 text-xs font-semibold text-amber-800">
              {simulation.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
          {simulation.rows.length > 0 ? (
            isDesktop ? (
              <div>
                <div
                  className="overflow-x-auto rounded-2xl bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)]"
                  role="region"
                  aria-label="模拟调仓明细，可横向滚动"
                  tabIndex={0}
                >
                  <table className="min-w-[40rem] text-left text-xs">
                    <caption className="sr-only">各基金当前仓位与模拟调仓后的金额、仓位变化</caption>
                    <thead className="bg-slate-50 text-slate-500">
                      <tr>
                        <th scope="col" className="px-3 py-2">基金</th>
                        <th scope="col" className="px-3 py-2">动作</th>
                        <th scope="col" className="px-3 py-2">变动</th>
                        <th scope="col" className="px-3 py-2">仓位%</th>
                        <th scope="col" className="px-3 py-2">模拟后%</th>
                      </tr>
                    </thead>
                    <tbody>
                      {simulation.rows.map((row) => (
                        <tr key={row.fund_code} className="border-t border-slate-100 text-slate-700">
                          <th scope="row" className="px-3 py-2 text-left font-semibold">{row.fund_name}</th>
                          <td className="px-3 py-2">{row.action}</td>
                          <td className="px-3 py-2">
                            <div>
                              {row.delta_yuan > 0 ? "+" : ""}
                              {row.delta_yuan.toLocaleString("zh-CN")}
                            </div>
                            {row.amount_note ? (
                              <div className="mt-0.5 max-w-[12rem] text-[10px] leading-4 text-slate-500">
                                {row.amount_note}
                              </div>
                            ) : null}
                          </td>
                          <td className="px-3 py-2">{row.current_weight_percent}</td>
                          <td className="px-3 py-2">
                            {row.simulated_weight_percent}
                            <span className="text-slate-500">
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
                <p className="mt-1 text-xs text-slate-500">表格内容较宽时可左右滚动查看。</p>
              </div>
            ) : (
              <div className="grid gap-2" role="list" aria-label="模拟调仓明细">
                {simulation.rows.map((row) => (
                  <article
                    key={row.fund_code}
                    role="listitem"
                    className="rounded-2xl border border-slate-200 bg-white p-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="break-words text-sm font-bold text-slate-900">
                          {row.fund_name}
                        </h4>
                        <p className="mt-0.5 text-xs text-slate-500">{row.fund_code}</p>
                      </div>
                      <span className="shrink-0 rounded-full bg-[var(--brand-soft)] px-2 py-1 text-xs font-bold text-[var(--brand-strong)]">
                        {row.action}
                      </span>
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                      <div>
                        <dt className="text-slate-500">金额变动</dt>
                        <dd className="mt-0.5 font-bold text-slate-800">
                          {row.delta_yuan > 0 ? "+" : ""}
                          {row.delta_yuan.toLocaleString("zh-CN")} 元
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate-500">当前仓位</dt>
                        <dd className="mt-0.5 font-bold text-slate-800">
                          {row.current_weight_percent}%
                        </dd>
                      </div>
                      <div className="col-span-2">
                        <dt className="text-slate-500">模拟后仓位</dt>
                        <dd className="mt-0.5 font-bold text-slate-800">
                          {row.simulated_weight_percent}%
                          <span className="ml-1 font-medium text-slate-500">
                            （{row.weight_delta_percent > 0 ? "+" : ""}
                            {row.weight_delta_percent} 个百分点）
                          </span>
                        </dd>
                      </div>
                    </dl>
                    {row.amount_note ? (
                      <p className="mt-2 break-words text-xs leading-5 text-slate-500">
                        {row.amount_note}
                      </p>
                    ) : null}
                  </article>
                ))}
              </div>
            )
          ) : (
            <InlineNotice tone="info" message="本报告暂无可模拟的调仓动作。" />
          )}
        </>
      ) : null}
    </div>
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
