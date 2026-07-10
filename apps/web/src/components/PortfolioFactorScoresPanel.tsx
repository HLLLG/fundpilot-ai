"use client";

import { useEffect, useState } from "react";
import type {
  FactorKey,
  FactorReliability,
  FundFactorScore,
  PortfolioFactorScores,
} from "@/lib/api";
import { fetchPortfolioFactorScores } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import {
  compositeSummary,
  factorLabel,
  factorPercentileHint,
  factorReliabilityTone,
  formatPercentile,
  gradeTone,
  percentileTone,
} from "@/lib/fundFactors";

const PRO_FLAG_KEY = "fundpilot-risk-metrics-pro";

// 因子展示顺序；momentum 为免费可见，其余 Pro 解锁
const FACTOR_ORDER: Array<{ key: FactorKey; pro: boolean }> = [
  { key: "momentum", pro: false },
  { key: "risk_adjusted", pro: true },
  { key: "drawdown", pro: true },
  { key: "size", pro: true },
];

function FactorBar({
  factorKey,
  percentile,
  locked,
  reliability,
}: {
  factorKey: FactorKey;
  percentile: number | null;
  locked: boolean;
  reliability?: FactorReliability | null;
}) {
  const tone = locked ? "neutral" : percentileTone(percentile);
  const width = percentile == null ? 0 : Math.max(2, Math.min(100, percentile));
  return (
    <div className={`factor-bar factor-tone-${tone}`}>
      <div className="factor-bar-head">
        <span className="factor-bar-label">
          {factorLabel(factorKey)}
          {reliability ? (
            <span
              className={`factor-ic-tag factor-tone-${factorReliabilityTone(reliability.level)}`}
              title={reliability.basis}
            >
              IC·{reliability.level}
            </span>
          ) : null}
        </span>
        <span className="factor-bar-value">
          {locked ? "•••" : formatPercentile(percentile)}
        </span>
      </div>
      <div className="factor-bar-track">
        <div className="factor-bar-fill" style={{ width: `${locked ? 0 : width}%` }} />
      </div>
      <div className="factor-bar-hint">
        {locked ? `升级「${BRAND.name} Pro」解锁` : factorPercentileHint(factorKey, percentile)}
      </div>
    </div>
  );
}

function FundCard({
  fund,
  isPro,
  reliability,
}: {
  fund: FundFactorScore;
  isPro: boolean;
  reliability?: Record<string, FactorReliability> | null;
}) {
  const tone = gradeTone(fund.composite_grade);
  return (
    <div className="factor-fund-card">
      <div className="factor-fund-head">
        <div className={`factor-grade-badge factor-tone-${tone}`}>
          {fund.composite_grade ?? "—"}
        </div>
        <div className="factor-fund-meta">
          <div className="factor-fund-name">
            {fund.fund_name || fund.fund_code}
            {!fund.in_universe ? <span className="factor-outside-tag">池外·按净值估算</span> : null}
          </div>
          <div className="factor-fund-score">
            综合 {fund.composite_score != null ? Math.round(fund.composite_score) : "—"}
          </div>
        </div>
      </div>
      <div className="factor-fund-summary">{compositeSummary(fund)}</div>
      <div className="factor-bars">
        {FACTOR_ORDER.map(({ key, pro }) => (
          <FactorBar
            key={key}
            factorKey={key}
            percentile={fund.factors[key]?.percentile ?? null}
            locked={pro && !isPro}
            reliability={reliability?.[key] ?? null}
          />
        ))}
      </div>
    </div>
  );
}

export function PortfolioFactorScoresPanel({ enabled }: { enabled: boolean }) {
  const [isPro, setIsPro] = useState(false);
  const [data, setData] = useState<PortfolioFactorScores | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 每次展开都重新读取 Pro 开关，与上方风险体检面板的 Pro 切换保持同步
    try {
      setIsPro(window.localStorage.getItem(PRO_FLAG_KEY) === "1");
    } catch {
      setIsPro(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled || data || loading) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPortfolioFactorScores()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载因子评分失败");
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
    return <div className="factor-note">正在拉取可比基金池并计算因子评分…</div>;
  }

  if (error) {
    return <div className="factor-note">因子评分加载失败：{error}</div>;
  }

  if (!data || !data.available) {
    return (
      <div className="factor-note">
        {data?.message ?? "暂无法计算因子评分（可比基金池数据不足）。"}
      </div>
    );
  }

  if (data.funds.length === 0) {
    return <div className="factor-note">暂无持仓可评分。</div>;
  }

  return (
    <div className="factor-panel-body">
      <div className="factor-cards">
        {data.funds.map((fund) => (
          <FundCard
            key={fund.fund_code}
            fund={fund}
            isPro={isPro}
            reliability={data.factor_reliability}
          />
        ))}
      </div>
      <div className="factor-foot">
        基准：排行榜可比池约 {data.universe_size} 只 · 横截面 z-score 百分位
        {isPro ? "" : " · 免费版仅展示动量，升级查看全部因子"}
      </div>
    </div>
  );
}
