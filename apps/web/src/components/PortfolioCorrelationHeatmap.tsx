"use client";

import { useEffect, useState } from "react";
import { InlineNotice } from "@/components/InlineNotice";
import type { PortfolioRiskCorrelation } from "@/lib/api";
import { fetchPortfolioRiskCorrelation } from "@/lib/api";

function corrCellStyle(value: number | null): React.CSSProperties {
  if (value == null) {
    return { background: "#f1f5f9", color: "#94a3b8" };
  }
  const intensity = Math.min(1, Math.abs(value));
  const alpha = intensity * 0.82 + 0.12;
  // 正相关（同涨同跌=假分散风险）偏玫红，负相关（对冲）偏绿
  const background =
    value >= 0 ? `rgba(225, 29, 72, ${alpha})` : `rgba(5, 150, 105, ${alpha})`;
  const color = intensity > 0.55 ? "#ffffff" : "#0f172a";
  return { background, color };
}

function shortName(name: string): string {
  return name.length > 6 ? `${name.slice(0, 6)}…` : name;
}

export function PortfolioCorrelationHeatmap({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<PortfolioRiskCorrelation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySequence, setRetrySequence] = useState(0);

  useEffect(() => {
    if (!enabled || data) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPortfolioRiskCorrelation()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载相关性失败");
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
  }, [enabled, data, retrySequence]);

  if (!enabled) {
    return null;
  }

  if (loading) {
    return <div className="risk-corr-note" role="status">正在拉取各持仓净值并计算相关性…</div>;
  }

  if (error) {
    return (
      <InlineNotice
        tone="error"
        message={`相关性加载失败：${error}`}
        action={{ label: "重试", onClick: () => setRetrySequence((current) => current + 1) }}
      />
    );
  }

  if (!data || !data.available) {
    return <div className="risk-corr-note">{data?.message ?? "暂无法计算相关性。"}</div>;
  }

  const { codes, names, matrix, max_pair: maxPair, sample_days: sampleDays } = data;

  return (
    <div className="risk-corr">
      <div
        className="overflow-x-auto rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2"
        role="region"
        aria-label="持仓相关性矩阵，可横向滚动"
        tabIndex={0}
      >
        <table className="w-full min-w-max border-separate border-spacing-0.5 text-center text-xs">
          <caption className="sr-only">各持仓近{sampleDays}个对齐交易日的收益相关系数</caption>
          <thead>
            <tr>
              <th scope="col" className="min-w-20 px-1 py-2 text-left text-xs font-bold text-slate-500">基金</th>
              {names.map((name, idx) => (
                <th
                  key={`col-${codes[idx]}`}
                  scope="col"
                  className="min-w-12 max-w-20 px-1 py-2 text-xs font-bold text-slate-600"
                  title={name}
                >
                  {shortName(name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {codes.map((code, row) => (
              <tr key={`row-${code}`}>
                <th
                  scope="row"
                  className="max-w-28 px-1 py-2 text-left text-xs font-bold text-slate-600"
                  title={names[row]}
                >
                  {shortName(names[row])}
                </th>
                {(matrix[row] ?? []).map((value, col) => (
                  <td
                    key={`cell-${code}-${codes[col]}`}
                    aria-label={`${names[row]}与${names[col]}相关系数${value == null ? "暂无数据" : value.toFixed(2)}`}
                    className="p-0.5"
                  >
                    <span className="risk-corr-cell" style={corrCellStyle(value)}>
                      {value == null ? "—" : value.toFixed(2)}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-500 sm:hidden">可左右滑动查看完整矩阵</p>

      {maxPair ? (
        <div className="risk-corr-insight">
          {maxPair.corr >= 0.9
            ? `「${maxPair.name_a}」与「${maxPair.name_b}」相关性 ${maxPair.corr.toFixed(2)}，几乎同涨同跌，等于重仓一个方向，建议分散到低相关板块。`
            : `相关性最高的是「${maxPair.name_a}」与「${maxPair.name_b}」（${maxPair.corr.toFixed(2)}）。`}
        </div>
      ) : null}
      <div className="risk-corr-foot">
        基于近 {sampleDays} 个对齐交易日的日收益 · 红=同向 / 绿=反向
      </div>
    </div>
  );
}
