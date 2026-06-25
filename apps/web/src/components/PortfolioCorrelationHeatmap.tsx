"use client";

import { useEffect, useState } from "react";
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

  useEffect(() => {
    if (!enabled || data || loading) {
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
  }, [enabled, data, loading]);

  if (!enabled) {
    return null;
  }

  if (loading) {
    return <div className="risk-corr-note">正在拉取各持仓净值并计算相关性…</div>;
  }

  if (error) {
    return <div className="risk-corr-note">相关性加载失败：{error}</div>;
  }

  if (!data || !data.available) {
    return <div className="risk-corr-note">{data?.message ?? "暂无法计算相关性。"}</div>;
  }

  const { codes, names, matrix, max_pair: maxPair, sample_days: sampleDays } = data;

  return (
    <div className="risk-corr">
      <div
        className="risk-corr-grid"
        style={{ gridTemplateColumns: `auto repeat(${codes.length}, minmax(2.25rem, 1fr))` }}
      >
        <div className="risk-corr-corner" />
        {names.map((name, idx) => (
          <div key={`col-${codes[idx]}`} className="risk-corr-col-label" title={name}>
            {shortName(name)}
          </div>
        ))}
        {codes.map((code, row) => (
          <FragmentRow
            key={`row-${code}`}
            label={names[row]}
            cells={matrix[row]}
            codes={codes}
          />
        ))}
      </div>

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

function FragmentRow({
  label,
  cells,
  codes,
}: {
  label: string;
  cells: Array<number | null>;
  codes: string[];
}) {
  return (
    <>
      <div className="risk-corr-row-label" title={label}>
        {shortName(label)}
      </div>
      {cells.map((value, col) => (
        <div key={`cell-${codes[col]}`} className="risk-corr-cell" style={corrCellStyle(value)}>
          {value == null ? "—" : value.toFixed(2)}
        </div>
      ))}
    </>
  );
}
