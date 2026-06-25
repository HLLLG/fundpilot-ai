"use client";

import { useEffect, useState } from "react";
import type { PortfolioRiskMetrics } from "@/lib/api";
import { PortfolioCorrelationHeatmap } from "@/components/PortfolioCorrelationHeatmap";
import {
  alphaHint,
  alphaTone,
  betaHint,
  betaTone,
  concentrationHint,
  concentrationTone,
  formatRatio,
  formatSignedPercent,
  maxDrawdownHint,
  maxDrawdownTone,
  type MetricTone,
  sharpeHint,
  sharpeTone,
  sortinoHint,
  volatilityHint,
  volatilityTone,
} from "@/lib/riskMetrics";

const PRO_FLAG_KEY = "fundpilot-risk-metrics-pro";

type MetricItem = {
  key: string;
  label: string;
  value: string;
  hint: string;
  tone: MetricTone;
  pro: boolean;
};

function buildItems(metrics: PortfolioRiskMetrics): MetricItem[] {
  return [
    {
      key: "max_drawdown",
      label: "最大回撤",
      value: formatSignedPercent(metrics.max_drawdown_percent),
      hint: maxDrawdownHint(metrics.max_drawdown_percent),
      tone: maxDrawdownTone(metrics.max_drawdown_percent),
      pro: false,
    },
    {
      key: "effective_holdings",
      label: "有效持仓数",
      value:
        metrics.effective_holdings != null ? `${metrics.effective_holdings.toFixed(1)} 只` : "—",
      hint: concentrationHint(metrics.hhi, metrics.effective_holdings),
      tone: concentrationTone(metrics.hhi),
      pro: false,
    },
    {
      key: "sharpe",
      label: "夏普比率",
      value: formatRatio(metrics.sharpe_ratio),
      hint: sharpeHint(metrics.sharpe_ratio),
      tone: sharpeTone(metrics.sharpe_ratio),
      pro: true,
    },
    {
      key: "sortino",
      label: "索提诺比率",
      value: formatRatio(metrics.sortino_ratio),
      hint: sortinoHint(metrics.sortino_ratio),
      tone: sharpeTone(metrics.sortino_ratio),
      pro: true,
    },
    {
      key: "volatility",
      label: "年化波动率",
      value: formatSignedPercent(metrics.annualized_volatility_percent),
      hint: volatilityHint(metrics.annualized_volatility_percent),
      tone: volatilityTone(metrics.annualized_volatility_percent),
      pro: true,
    },
    {
      key: "beta",
      label: "Beta（对沪深300）",
      value: formatRatio(metrics.beta),
      hint: betaHint(metrics.beta),
      tone: betaTone(metrics.beta),
      pro: true,
    },
    {
      key: "alpha",
      label: "Alpha（超额）",
      value: formatSignedPercent(metrics.alpha_percent),
      hint: alphaHint(metrics.alpha_percent),
      tone: alphaTone(metrics.alpha_percent),
      pro: true,
    },
  ];
}

function MetricCard({ item, locked }: { item: MetricItem; locked: boolean }) {
  return (
    <div className={`risk-card risk-tone-${item.tone}${locked ? " risk-card-locked" : ""}`}>
      <div className="risk-card-label">{item.label}</div>
      <div className="risk-card-value">{locked ? "•••" : item.value}</div>
      <div className="risk-card-hint">{locked ? "升级「好基灵 Pro」解锁" : item.hint}</div>
    </div>
  );
}

export function PortfolioRiskMetricsPanel({
  metrics,
}: {
  metrics: PortfolioRiskMetrics | undefined;
}) {
  const [isPro, setIsPro] = useState(false);
  const [showCorrelation, setShowCorrelation] = useState(false);

  useEffect(() => {
    try {
      setIsPro(window.localStorage.getItem(PRO_FLAG_KEY) === "1");
    } catch {
      setIsPro(false);
    }
  }, []);

  const togglePro = () => {
    setIsPro((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(PRO_FLAG_KEY, next ? "1" : "0");
      } catch {
        /* ignore storage errors */
      }
      return next;
    });
  };

  return (
    <section className="pl-panel section-card">
      <div className="pl-panel-head">
        <div className="pl-panel-title">组合风险体检</div>
        <button type="button" className="risk-pro-toggle" onClick={togglePro}>
          {isPro ? "Pro 已解锁" : "升级解锁"}
        </button>
      </div>

      {!metrics || !metrics.available ? (
        <div className="empty-state">
          {metrics?.message ?? "历史快照积累中，满 20 个交易日后展示风险体检。"}
        </div>
      ) : (
        <>
          <div className="risk-metrics-grid">
            {buildItems(metrics).map((item) => (
              <MetricCard key={item.key} item={item} locked={item.pro && !isPro} />
            ))}
          </div>
          <div className="risk-metrics-foot">
            样本 {metrics.sample_days} 个交易日 · 年化收益{" "}
            {formatSignedPercent(metrics.annualized_return_percent)}
            {isPro ? "" : " · 免费版仅展示回撤与分散度，升级查看全部"}
          </div>

          <div className="risk-corr-section">
            {!isPro ? (
              <button type="button" className="risk-corr-toggle" onClick={togglePro}>
                🔒 持仓相关性矩阵（Pro）— 看清是否「假分散」
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="risk-corr-toggle"
                  aria-expanded={showCorrelation}
                  onClick={() => setShowCorrelation((value) => !value)}
                >
                  {showCorrelation ? "收起持仓相关性矩阵" : "展开持仓相关性矩阵"}
                </button>
                <PortfolioCorrelationHeatmap enabled={showCorrelation} />
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}
